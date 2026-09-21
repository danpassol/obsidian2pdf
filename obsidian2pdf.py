#!/usr/bin/env python3
"""obsidian2pdf: exporta notas de Obsidian con `status: finished` a PDF corporativo.

Flujo: frontmatter -> status == finished? -> preprocesado Obsidian -> pandoc (HTML)
       -> WeasyPrint (PDF con portada, índice, cabecera y pie).
Solo reexporta si el contenido de la nota ha cambiado desde la última vez.
"""
import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from string import Template
from urllib.parse import quote, unquote, urlsplit

ASSETS = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path.home() / ".config/obsidian2pdf/config.toml"
OBSIDIAN_JSON = [
    Path.home() / ".config/obsidian/obsidian.json",
    Path.home() / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json",  # Flatpak
    Path.home() / "snap/obsidian/current/.config/obsidian/obsidian.json",  # Snap
]
# extensiones de pandoc que necesita la exportación (pandoc antiguos no las tienen)
PANDOC_EXTENSIONS = ["mark", "task_lists", "lists_without_preceding_blankline", "hard_line_breaks"]
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}

DEFAULTS = {
    "general": {
        "output_mode": "alongside",  # alongside: PDF junto a la nota | central: todo en output_dir
        "output_dir": "~/Documentos/PDF",  # solo se usa con output_mode = "central"
        "state_file": "~/.local/state/obsidian2pdf/state.json",
        "status_field": "status",
        "export_status": "finished",
        "exclude_dirs": [".obsidian", ".trash", ".git"],
        "auto_detect_vaults": False,
        "watch_interval": 30,  # segundos entre comprobaciones en modo --watch (mínimo 5)
    },
    "defaults": {"author": "", "author_email": ""},
    "branding": {
        "company": "Mi Empresa",
        "logo": "",
        "primary_color": "#1f3a5f",
        "accent_color": "#e07b00",
        "font": '"Liberation Sans", "DejaVu Sans", sans-serif',
        "confidential": "Documento confidencial",
        "toc_title": "Índice",
        "page_label": "Página",
        "date_format": "%d/%m/%Y",
        "lang": "es",
        "highlight_style": "pygments",  # resaltado de código: pygments, tango, kate, monochrome, zenburn... | none
        "justify": True,  # texto justificado (párrafos, listas, citas, callouts, tablas)
        "hyphenate": True,  # partir palabras con guiones al justificar (según lang)
        "label_author": "Autor",
        "label_date": "Fecha",
        "label_version": "Versión",
        "label_company": "Organización",
    },
}

log = logging.getLogger("obsidian2pdf")


class ExportError(Exception):
    """Fallo esperado al exportar una nota (se registra sin traceback)."""


# ---------------------------------------------------------------- dependencias


def missing_pandoc_extensions() -> list:
    """Extensiones de PANDOC_EXTENSIONS que el pandoc instalado no soporta."""
    try:
        out = subprocess.run(
            ["pandoc", "--list-extensions=markdown"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []  # no se puede saber: que falle (con su mensaje) al exportar
    supported = {line.lstrip("+-").strip() for line in out.splitlines()}
    return [e for e in PANDOC_EXTENSIONS if e not in supported]


def check_dependencies(cfg: dict) -> list:
    """Devuelve la lista de problemas que impiden exportar (vacía si todo está bien)."""
    problems = []
    for mod, pkg in (("yaml", "python-yaml / PyYAML"), ("weasyprint", "weasyprint")):
        try:
            __import__(mod)
        except ImportError:
            problems.append(f"Falta el módulo Python '{mod}' (instala {pkg})")
    if not shutil.which("pandoc"):
        problems.append("No se encuentra 'pandoc' en el PATH (instala pandoc)")
    else:
        missing = missing_pandoc_extensions()
        if missing:
            problems.append(
                "Tu pandoc es demasiado antiguo: no soporta las extensiones %s. "
                "Instala una versión reciente (https://pandoc.org/installing.html)" % ", ".join(missing))
    for name in ("style.css", "template.html"):
        if not (ASSETS / name).is_file():
            problems.append(f"Falta {ASSETS / name}")
    logo = cfg["branding"].get("logo")
    if logo and not Path(logo).expanduser().is_file():
        log.warning("El logo %s no existe; se usará el texto de confidencialidad en el pie", logo)
    return problems

# ---------------------------------------------------------------- config / estado


def load_config(path: Path) -> dict:
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    cfg["vaults"] = []
    if path.exists():
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for section, values in user.items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
            else:
                cfg[section] = values
    else:
        log.warning("No existe %s, usando valores por defecto", path)
    return cfg


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def detect_obsidian_vaults() -> list:
    """Bóvedas registradas por Obsidian (nativo o Flatpak)."""
    found = []
    for f in OBSIDIAN_JSON:
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for v in (data.get("vaults") or {}).values():
            if isinstance(v, dict) and v.get("path"):
                found.append(Path(v["path"]))
    return found


def assign_labels(entries: list) -> list:
    """entries: [(ruta, nombre_explícito|None)] -> [(ruta, etiqueta)].

    La etiqueta es el nombre de la subcarpeta de salida. Sin nombre explícito se usa
    el nombre de la carpeta y, si choca con otra bóveda, se le van añadiendo carpetas
    padre (acme/docs, beta/docs...) hasta que sea única.
    """
    parts = {p: p.resolve().parts for p, _ in entries}
    explicit = {p: n for p, n in entries if n}
    auto = [p for p, n in entries if not n]
    depth = {p: 1 for p in auto}
    while True:
        cur = {p: "/".join(parts[p][-depth[p]:]) for p in auto}
        counts = Counter(list(cur.values()) + list(explicit.values()))
        clash = [p for p in auto if counts[cur[p]] > 1 and depth[p] < len(parts[p]) - 1]
        if not clash:
            break
        for p in clash:
            depth[p] += 1
    return [(p, explicit.get(p) or cur[p]) for p, _ in entries]


def resolve_vaults(cfg: dict) -> list:
    """Devuelve [(ruta, etiqueta)] sin duplicados."""
    entries = []
    for v in cfg["vaults"]:
        if isinstance(v, dict):
            entries.append((Path(v["path"]).expanduser(), v.get("name")))
        else:
            entries.append((Path(v).expanduser(), None))
    if cfg["general"].get("auto_detect_vaults"):
        entries += [(p, None) for p in detect_obsidian_vaults()]
    seen, unique = set(), []
    for p, n in entries:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            unique.append((p, n))
    return assign_labels(unique)


# ---------------------------------------------------------------- lectura de notas

FM_RE = re.compile(r"\A---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.DOTALL)


def split_frontmatter(text: str):
    text = text.replace("\r\n", "\n")
    m = FM_RE.match(text)
    if not m:
        return {}, text
    import yaml

    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        log.warning("Frontmatter YAML inválido: %s", e)
        return {}, text
    return (meta if isinstance(meta, dict) else {}), text[m.end():]


def scan_vault(vault: Path, exclude: set):
    """Devuelve (lista de .md, índice nombre->ruta del resto de ficheros)."""
    notes, files = [], {}
    for root, dirs, names in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in exclude and not d.startswith(".")]
        for n in names:
            p = Path(root) / n
            if n.lower().endswith(".md"):
                notes.append(p)
            else:
                files.setdefault(n.lower(), p)
    return notes, files


# ---------------------------------------------------------------- sintaxis Obsidian

FENCE_RE = re.compile(r"(^(?:```|~~~).*?^(?:```|~~~)[ \t]*$)", re.DOTALL | re.MULTILINE)
EMBED_RE = re.compile(r"!\[\[([^\]|#]+?)(?:#([^\]|]*))?(?:\|([^\]]*))?\]\]")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
MAX_EMBED_DEPTH = 5
WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]*))?\]\]")
COMMENT_RE = re.compile(r"%%.*?%%", re.DOTALL)
CALLOUT_RE = re.compile(r"^>\s*\[!([\w-]+)\][+-]?[ \t]*(.*)$")
FENCE_OPEN_RE = re.compile(r"^((?:```+|~~~+)[ \t]*)([\w+#-]+)", re.MULTILINE)
# nombres habituales en Obsidian/GitHub que pandoc no reconoce -> nombre que sí conoce
LANG_ALIASES = {
    "shell": "bash", "console": "bash", "terminal": "bash", "shellscript": "bash",
    "tsx": "typescript", "golang": "go", "hcl": "terraform", "jsonc": "json",
    "docker": "dockerfile", "vue": "html", "svelte": "html", "c#": "cs",
}


def normalize_fences(text: str) -> str:
    """Traduce el lenguaje de los bloques de código a uno que pandoc sepa resaltar."""
    return FENCE_OPEN_RE.sub(
        lambda m: m.group(1) + LANG_ALIASES.get(m.group(2).lower(), m.group(2)), text
    )


INLINE_CODE_RE = re.compile(r"``[^\n]+?``|`[^`\n]+`")


def outside_code(text: str, fn) -> str:
    """Aplica fn solo fuera del código: bloques con fence y código en línea.

    El código en línea se sustituye por un marcador mientras se transforma (en vez de
    partir el texto) para no romper líneas que fn trata en conjunto, como los callouts.
    """

    def apply(chunk):
        spans = []

        def stash(m):
            spans.append(m.group(0))
            return f"\x00{len(spans) - 1}\x00"

        out = fn(INLINE_CODE_RE.sub(stash, chunk))
        return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], out)

    parts = FENCE_RE.split(text)
    return "".join(p if i % 2 else apply(p) for i, p in enumerate(parts))


def convert_callouts(text: str) -> str:
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        m = CALLOUT_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        kind = m.group(1).lower()
        title = m.group(2).strip() or kind.capitalize()
        i += 1
        body = []
        while i < len(lines) and lines[i].startswith(">"):
            body.append(re.sub(r"^>\s?", "", lines[i]))
            i += 1
        out += [f"::: {{.callout .callout-{kind}}}", f"[{title}]{{.callout-title}}", "", *body, ":::", ""]
    return "\n".join(out)


class Links:
    """Resuelve wikilinks a PDFs de otras notas exportables de la misma bóveda."""

    def __init__(self, notes, files, is_exportable, pdf_path):
        self.by_stem = {}
        for n in sorted(notes):
            self.by_stem.setdefault(n.stem.lower(), []).append(n)
        self.files = files
        self.is_exportable = is_exportable  # nota -> bool
        self.pdf_path = pdf_path  # nota -> Path del PDF

    def resolve(self, target: str, current: Path):
        """Ruta a la que enlazar, o None (se deja como texto plano)."""
        name = target.split("#")[0].strip()
        if not name:
            return None  # [[#Sección]]: enlace dentro de la propia nota
        if Path(name).suffix.lower() == ".pdf":  # [[Documento.pdf]]
            return self.files.get(Path(name).name.lower())
        note = self.find_note(name, current)
        return self.pdf_path(note) if note and self.is_exportable(note) else None

    def find_note(self, name: str, current: Path):
        """Nota a la que apunta [[name]] (nombre, con o sin .md, o carpeta/nombre)."""
        stem = name[:-3] if name.lower().endswith(".md") else name
        cands = self.by_stem.get(Path(stem).name.lower(), [])
        if "/" in stem:  # [[carpeta/Nota]]
            cands = [c for c in cands if c.with_suffix("").as_posix().lower().endswith(stem.lower())]
        cands = [c for c in cands if c != current]
        cands.sort(key=lambda c: c.parent != current.parent)  # a igualdad, la de la misma carpeta
        return cands[0] if cands else None


def extract_section(body: str, section: str) -> str:
    """Trozo de `body` que corresponde a `#Título` (hasta el siguiente título de igual o
    mayor nivel) o a un bloque `#^id`. Devuelve None si no se encuentra."""
    lines = body.split("\n")
    if section.startswith("^"):
        marker = section
        blocks = re.split(r"\n[ \t]*\n", body)
        for b in blocks:
            if re.search(re.escape(marker) + r"[ \t]*$", b.strip()):
                return re.sub(r"[ \t]*" + re.escape(marker) + r"[ \t]*$", "", b.strip())
        return None
    wanted = section.strip().lower()
    start = level = None
    in_fence = False
    for i, line in enumerate(lines):
        if re.match(r"^(```|~~~)", line):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if not m:
            continue
        if start is None:
            if m.group(2).strip().lower() == wanted:
                start, level = i, len(m.group(1))
        elif len(m.group(1)) <= level:
            return "\n".join(lines[start:i]).strip()
    return "\n".join(lines[start:]).strip() if start is not None else None


def expand_embeds(body: str, links, current: Path, stack=()) -> str:
    """Sustituye ![[Nota]] / ![[Nota#Sección]] por el contenido de esa nota.

    Las imágenes y PDFs no se tocan (los resuelve preprocess). Los embeds que no se
    pueden resolver se dejan como un aviso en texto plano.
    """

    def embed(m):
        name = m.group(1).strip()
        if Path(name).suffix.lower() in IMG_EXT | {".pdf"}:
            return m.group(0)
        note = links.find_note(name, current)
        if not note:
            return f"*[nota no encontrada: {name}]*"
        if note in stack or len(stack) >= MAX_EMBED_DEPTH:
            return f"*[embed circular o demasiado profundo: {name}]*"
        try:
            _, inner = split_frontmatter(note.read_text(encoding="utf-8"))
        except OSError:
            return f"*[no se pudo leer: {name}]*"
        if m.group(2):
            section = extract_section(inner, m.group(2))
            if section is None:
                return f"*[sección no encontrada: {name}#{m.group(2)}]*"
            inner = section
        inner = expand_embeds(inner, links, note, (*stack, note))
        return f"\n\n::: {{.embed}}\n{inner.strip()}\n:::\n\n"

    return outside_code(body, lambda chunk: EMBED_RE.sub(embed, chunk))


def preprocess(body: str, files: dict, links=None, current=None) -> str:
    def embed(m):
        name = Path(m.group(1).strip()).name
        alias = (m.group(3) or "").strip()
        if Path(name).suffix.lower() in IMG_EXT:
            p = files.get(name.lower())
            if not p:
                return f"*[imagen no encontrada: {name}]*"
            width = ""
            wm = re.match(r"^(\d+)(?:x\d+)?$", alias)
            if wm:
                width = f"{{width={wm.group(1)}px}}"
            img = f"![]({p.resolve().as_uri()}){width}"
            line_start = m.string.rfind("\n", 0, m.start()) + 1
            line_end = m.string.find("\n", m.end())
            alone = not m.string[line_start:m.start()] and not m.string[m.end():line_end if line_end != -1 else None].strip()
            # imagen sola en su línea: se centra (pandoc no crea <figure> si no hay pie)
            return f"\n::: {{.img-center}}\n{img}\n:::\n" if alone else img
        return f"*{Path(name).stem}*"  # notas/PDF embebidos: no soportado, se deja el nombre

    def link(m):
        target, alias = m.group(1).strip(), (m.group(2) or "").strip()
        text = alias or target.split("#")[0] or target.lstrip("#")
        dest = links.resolve(target, current) if links else None
        return f"[{text}]({dest.resolve().as_uri()})" if dest else text

    def transform(chunk):
        chunk = COMMENT_RE.sub("", chunk)
        chunk = EMBED_RE.sub(embed, chunk)
        chunk = WIKILINK_RE.sub(link, chunk)
        return convert_callouts(chunk)

    if links:
        body = expand_embeds(body, links, current, (current,))
    return outside_code(normalize_fences(body), transform)


# ---------------------------------------------------------------- render


def css_string(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


# Iconos 24x24 de trazo (estilo Lucide); {c} se sustituye por el color del callout.
_ICONS = {
    "pencil": '<path d="M17 3l4 4L8 20H4v-4z"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>',
    "todo": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12l3 3 5-6"/>',
    "clipboard": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3h6v3H9zM9 11h6M9 15h6"/>',
    "bulb": '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10c1 1 1 2 1 3h6c0-1 0-2 1-3a6 6 0 0 0-4-10z"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17v.01"/>',
    "warning": '<path d="M12 3L2 20h20z"/><path d="M12 10v4M12 17v.01"/>',
    "x": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "zap": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "bug": '<ellipse cx="12" cy="14" rx="4" ry="6"/><path d="M12 8V4M8 11H4M16 11h4M8 16H4M16 16h4M9 8l-2-3M15 8l2-3"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6v.01M3 12v.01M3 18v.01"/>',
    "quote": '<path fill="{c}" stroke="none" d="M4 12c0-4 2-6 5-7v2c-2 1-3 2-3 4h3v6H4zM14 12c0-4 2-6 5-7v2c-2 1-3 2-3 4h3v6h-5z"/>',
}
# (tipos y alias de Obsidian, color, fondo, icono). None en el color = color primario.
CALLOUT_STYLES = [
    (["note"], None, "#eef3f9", "pencil"),
    (["info"], "#2563eb", "#eef4ff", "info"),
    (["todo"], "#2563eb", "#eef4ff", "todo"),
    (["abstract", "summary", "tldr"], "#0d9488", "#ebf8f6", "clipboard"),
    (["tip", "hint", "important"], "#0891b2", "#e9f7fb", "bulb"),
    (["success", "check", "done"], "#16a34a", "#effaf2", "check"),
    (["question", "help", "faq"], "#ca8a04", "#fefce8", "help"),
    (["warning", "caution", "attention"], "#d97706", "#fff8eb", "warning"),
    (["failure", "fail", "missing"], "#dc2626", "#fdf0f0", "x"),
    (["danger", "error"], "#dc2626", "#fdf0f0", "zap"),
    (["bug"], "#dc2626", "#fdf0f0", "bug"),
    (["example"], "#7c3aed", "#f5f0ff", "list"),
    (["quote", "cite"], "#6b7280", "#f5f5f6", "quote"),
]


def icon_url(name: str, color: str) -> str:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + _ICONS[name] + "</svg>"
    ).replace("{c}", color)
    return 'url("data:image/svg+xml,' + quote(svg, safe="") + '")'


def callout_css(primary: str) -> str:
    """CSS de color e icono para cada tipo de callout (los desconocidos usan el de `note`)."""
    rules = [f".callout-title::before {{ background-image: {icon_url('pencil', primary)}; }}"]
    for names, color, bg, icon in CALLOUT_STYLES:
        color = color or primary
        cls = lambda suffix="": ", ".join(f".callout-{n}{suffix}" for n in names)
        rules.append(f"{cls()} {{ border-color: {color}; background: {bg}; }}")
        rules.append(f"{cls(' .callout-title')} {{ color: {color}; }}")
        rules.append(f"{cls(' .callout-title::before')} {{ background-image: {icon_url(icon, color)}; }}")
    return "\n".join(rules)


def logo_uri(brand: dict) -> str:
    """URI del logo configurado, o "" si no hay logo o no existe."""
    logo = Path(brand["logo"]).expanduser() if brand["logo"] else None
    return logo.resolve().as_uri() if logo and logo.is_file() else ""


def footer_left(brand: dict) -> str:
    """Contenido del pie izquierdo: el logo si existe; si no, el texto confidencial."""
    uri = logo_uri(brand)
    if uri:
        return f"content: \"\"; background: url({css_string(uri)}) no-repeat left 3mm; background-size: auto 9mm;"
    return f"content: {css_string(brand['confidential'])};"


def render_css(brand: dict):
    from weasyprint import CSS

    tpl = (ASSETS / "style.css").read_text()
    css = Template(tpl).safe_substitute(
        primary=brand["primary_color"],
        accent=brand["accent_color"],
        font=brand["font"],
        company_q=css_string(brand["company"]),
        footer_left=footer_left(brand),
        callout_css=callout_css(brand["primary_color"]),
        toc_title_q=css_string(brand["toc_title"]),
        page_label_q=css_string(brand["page_label"] + " "),
        text_align="justify" if brand["justify"] else "left",
        hyphens="auto" if brand["hyphenate"] else "manual",
    )
    return CSS(string=css)


def relative_uri(uri: str, out_dir: Path) -> str:
    """file:///abs/x.pdf -> x.pdf, relativo a la carpeta del PDF generado."""
    parts = urlsplit(uri)
    rel = os.path.relpath(unquote(parts.path), out_dir)
    return quote(Path(rel).as_posix()) + (f"#{parts.fragment}" if parts.fragment else "")


def make_link_finisher(out_dir: Path):
    """Reescribe los enlaces file:// a PDFs locales como relativos (el PDF sigue
    funcionando si se mueve la carpeta)."""

    def finisher(document, pdf):
        import pydyf

        for obj in pdf.objects:
            if not isinstance(obj, pydyf.Dictionary):
                continue
            action = obj.get("A")  # la acción va dentro de la anotación de enlace
            if not isinstance(action, pydyf.Dictionary) or action.get("S") != "/URI":
                continue
            uri = getattr(action.get("URI"), "string", "")
            if uri.startswith("file://") and urlsplit(uri).path.lower().endswith(".pdf"):
                action["URI"] = pydyf.String(relative_uri(uri, out_dir))

    return finisher


def fmt_date(value, fmt: str, fallback: Path) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime(fmt)
    if value:
        return str(value)
    return datetime.fromtimestamp(fallback.stat().st_mtime).strftime(fmt)


def pandoc_version() -> tuple:
    try:
        out = subprocess.run(["pandoc", "--version"], capture_output=True, text=True).stdout
        return tuple(int(x) for x in re.search(r"pandoc (\d+(?:\.\d+)*)", out).group(1).split("."))
    except (OSError, AttributeError, ValueError):
        return (0,)


def highlight_args(style: str) -> list:
    """Argumentos de pandoc para el estilo de resaltado (la opción cambió de nombre en 3.8)."""
    if str(style).lower() == "none":
        return ["--no-highlight"] if pandoc_version() < (3, 8) else ["--syntax-highlighting=none"]
    flag = "--highlight-style" if pandoc_version() < (3, 8) else "--syntax-highlighting"
    return [f"{flag}={style}"]


def run_pandoc(cmd: list, body: str) -> str:
    try:
        res = subprocess.run(cmd, input=body, capture_output=True, text=True)
    except FileNotFoundError:
        raise ExportError("no se encuentra 'pandoc' en el PATH") from None
    if res.returncode != 0:
        raise ExportError(f"pandoc falló (código {res.returncode}):\n{res.stderr.strip()}")
    if res.stderr.strip():
        log.warning("pandoc: %s", res.stderr.strip())
    return res.stdout


def build_pdf(note: Path, meta: dict, body: str, cfg: dict, out: Path) -> None:
    from weasyprint import HTML

    brand, defaults = cfg["branding"], cfg["defaults"]
    fields = {
        "title": meta.get("title") or note.stem,
        "subtitle": meta.get("subtitle", ""),
        "author": meta.get("author") or defaults["author"],
        "author_email": meta.get("author_email") or defaults["author_email"],
        "date": fmt_date(meta.get("date"), brand["date_format"], note),
        "version": meta.get("version", ""),
        "company": brand["company"],
        "logo": logo_uri(brand),
        "lang": brand["lang"],
        **{k: brand[k] for k in ("label_author", "label_date", "label_version", "label_company")},
    }
    cmd = [
        "pandoc",
        "-f", "markdown+" + "+".join(PANDOC_EXTENSIONS),
        "-t", "html5", "-s", "--toc", "--toc-depth=3", "--mathml",
        "--template", str(ASSETS / "template.html"),
        *highlight_args(brand["highlight_style"]),
    ]
    for k, v in fields.items():
        if v not in ("", None):
            cmd += ["-M", f"{k}={v}"]
    html = run_pandoc(cmd, body)

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=out.parent, suffix=".pdf.tmp", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        HTML(string=html, base_url=str(note.parent) + "/").write_pdf(
            tmp_path, stylesheets=[render_css(brand)], finisher=make_link_finisher(out.parent),
            presentational_hints=True,  # sin esto WeasyPrint ignora width="320" de pandoc
        )
        tmp_path.replace(out)
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------- proceso principal


def is_ours(note: Path, state: dict) -> bool:
    """El PDF de una nota es nuestro si ya la hemos exportado antes (consta en el estado).

    No se puede buscar una marca dentro del PDF: WeasyPrint comprime los metadatos.
    """
    return str(note) in state


def out_path(note: Path, vault: Path, label: str, gen: dict) -> Path:
    if gen["output_mode"] == "alongside":
        return note.with_suffix(".pdf")
    out_dir = Path(gen["output_dir"]).expanduser() / label
    return (out_dir / note.relative_to(vault)).with_suffix(".pdf")


def is_exportable(note: Path, gen: dict, cache: dict) -> bool:
    """True si la nota tiene status == export_status (con caché)."""
    if note not in cache:
        try:
            meta, _ = split_frontmatter(note.read_text(encoding="utf-8"))
        except OSError:
            meta = {}
        cache[note] = str(meta.get(gen["status_field"], "")).strip().lower() == gen["export_status"].lower()
    return cache[note]


def make_links(notes, files, vault, label, gen) -> Links:
    cache = {}
    return Links(
        notes, files,
        lambda n: is_exportable(n, gen, cache),
        lambda n: out_path(n, vault, label, gen),
    )


def process_note(note, vault, label, links, cfg, state, force, ignore_status=False) -> bool:
    gen = cfg["general"]
    text = note.read_text(encoding="utf-8")
    if not ignore_status and not text.startswith("---"):
        return False
    meta, body = split_frontmatter(text)
    status = str(meta.get(gen["status_field"], "")).strip().lower()
    if not ignore_status and status != gen["export_status"].lower():
        return False

    expanded = expand_embeds(body, links, note, (note,))
    # si hay embeds, el hash incluye su contenido: cambiar una nota incrustada reexporta esta
    digest = hashlib.sha256((text if expanded == body else text + "\0" + expanded).encode()).hexdigest()
    out = out_path(note, vault, label, gen)
    if not force and state.get(str(note)) == digest and out.exists():
        return False
    if out.exists() and not is_ours(note, state):
        skip_key = f"skipped:{note}"  # avisar una sola vez por versión de la nota
        if state.get(skip_key) != digest:
            log.warning(
                "Omitida %s: ya existe %s y no consta que lo generase obsidian2pdf "
                "(bórralo o renómbralo para exportar)", note.name, out.name)
            state[skip_key] = digest
        return False

    log.info("Exportando %s -> %s", note.relative_to(vault), out)
    build_pdf(note, meta, preprocess(expanded, links.files, links, note), cfg, out)
    state[str(note)] = digest
    return True


def export_vaults(cfg, state, force, stop=None, failed=None) -> tuple:
    """Una pasada por todas las bóvedas. Devuelve (exportadas, errores).

    `failed` (solo en --watch): {nota: mtime} de las que fallaron, para no reintentarlas
    (ni repetir el error en el log) hasta que se editen.
    """
    gen = cfg["general"]
    exclude = set(gen["exclude_dirs"])
    exported = errors = 0
    for vault, label in resolve_vaults(cfg):
        if not vault.is_dir():
            log.warning("Bóveda no encontrada: %s", vault)
            continue
        notes, files = scan_vault(vault, exclude)
        links = make_links(notes, files, vault, label, gen)
        for note in notes:
            if stop is not None and stop.is_set():
                return exported, errors
            mtime = _mtime(note) if failed is not None else None
            if failed is not None and failed.get(note) == mtime:
                continue
            try:
                if process_note(note, vault, label, links, cfg, state, force):
                    exported += 1
                    failed and failed.pop(note, None)
                    save_state(Path(gen["state_file"]).expanduser(), state)
            except Exception as e:
                errors += 1
                if failed is not None:
                    failed[note] = mtime
                if isinstance(e, ExportError):
                    log.error("Fallo exportando %s: %s", note, e)
                else:
                    log.exception("Fallo exportando %s", note)
    return exported, errors


def watch(args, cfg_path: Path, state, state_path: Path) -> int:
    """Bucle permanente: cada `watch_interval` segundos exporta las notas nuevas o cambiadas."""
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    cfg, cfg_mtime, failed = load_config(cfg_path), _mtime(cfg_path), {}
    log.info("Vigilando cambios (cada %ss). Ctrl+C para salir", watch_interval(cfg))
    while not stop.is_set():
        before = json.dumps(state, sort_keys=True)
        export_vaults(cfg, state, args.force, stop, failed)
        if json.dumps(state, sort_keys=True) != before:
            save_state(state_path, state)  # p. ej. avisos de PDFs ajenos ya registrados
        args.force = False  # --force solo afecta a la primera pasada
        stop.wait(watch_interval(cfg))
        if _mtime(cfg_path) != cfg_mtime:  # config editada: recargar sin reiniciar
            cfg, cfg_mtime = load_config(cfg_path), _mtime(cfg_path)
            log.info("Configuración recargada")
    log.info("Parado")
    return 0


def watch_interval(cfg: dict) -> float:
    return max(5.0, float(cfg["general"]["watch_interval"]))


def _mtime(path: Path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--force", action="store_true", help="reexportar aunque no haya cambios")
    ap.add_argument("--file", type=Path, help="exportar una nota concreta (ignora status)")
    ap.add_argument("--watch", action="store_true",
                    help="no salir: vigilar las bóvedas y exportar al detectar cambios")
    ap.add_argument("-v", "--verbose", action="store_true", help="incluir el log detallado de WeasyPrint")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.verbose:
        for noisy in ("weasyprint", "fontTools"):
            logging.getLogger(noisy).setLevel(logging.ERROR)
    cfg = load_config(args.config)
    problems = check_dependencies(cfg)
    if problems:
        for pr in problems:
            log.error(pr)
        return 2
    gen = cfg["general"]
    state_path = Path(gen["state_file"]).expanduser()
    state = load_state(state_path)

    if args.file:
        vaults = resolve_vaults(cfg)
        note = args.file.expanduser().resolve()
        vault, label = next(
            ((v, l) for v, l in vaults if v.resolve() in note.parents),
            (note.parent, note.parent.name),
        )
        notes, files = scan_vault(vault, set(gen["exclude_dirs"]))
        try:
            process_note(note, vault, label, make_links(notes, files, vault, label, gen), cfg, state, True, ignore_status=True)
            save_state(state_path, state)
        except ExportError as e:
            log.error("Fallo exportando %s: %s", note, e)
            return 1
        return 0

    if args.watch:
        return watch(args, args.config, state, state_path)

    before = json.dumps(state, sort_keys=True)
    exported, errors = export_vaults(cfg, state, args.force)
    if json.dumps(state, sort_keys=True) != before:
        save_state(state_path, state)
    log.info("Hecho: %d exportadas, %d errores", exported, errors)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
