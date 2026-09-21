"""Tests de obsidian2pdf. Ejecutar: python3 -m unittest discover -s tests"""
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import obsidian2pdf as o  # noqa: E402

GEN = {"output_mode": "alongside", "status_field": "status", "export_status": "finished"}


def make_vault(tmp: Path, notes: dict) -> Path:
    for rel, text in notes.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp


def links_for(vault: Path, exclude=()):
    notes, files = o.scan_vault(vault, set(exclude))
    return o.make_links(notes, files, vault, "v", GEN), notes


class Frontmatter(unittest.TestCase):
    def test_split(self):
        meta, body = o.split_frontmatter("---\nstatus: finished\ntitle: X\n---\nHola")
        self.assertEqual(meta, {"status": "finished", "title": "X"})
        self.assertEqual(body, "Hola")

    def test_sin_frontmatter_o_invalido(self):
        self.assertEqual(o.split_frontmatter("Hola"), ({}, "Hola"))
        self.assertEqual(o.split_frontmatter("---\na: [\n---\nx")[0], {})


class Preprocess(unittest.TestCase):
    def test_comentarios_y_codigo(self):
        out = o.preprocess("a %%oculto%% b\n```\n[[NoTocar]] %%x%%\n```", {})
        self.assertIn("a  b", out)
        self.assertIn("[[NoTocar]] %%x%%", out)

    def test_wikilink_sin_resolver_es_texto(self):
        self.assertEqual(o.preprocess("[[Nota|alias]] y [[Otra#Sec]]", {}), "alias y Otra")

    def test_codigo_en_linea_no_se_procesa(self):
        src = "usa `[[Nota]]` y `![[x#Sec]]` y ``%%c%%`` pero [[Real]]"
        out = o.preprocess(src, {})
        self.assertIn("`[[Nota]]`", out)
        self.assertIn("`![[x#Sec]]`", out)
        self.assertIn("``%%c%%``", out)
        self.assertTrue(out.endswith("pero Real"))

    def test_codigo_en_linea_dentro_de_callout(self):
        out = o.preprocess("> [!tip] Usa `x`\n> cuerpo con `y` y más\n> tercera línea", {})
        self.assertIn("[Usa `x`]{.callout-title}", out)
        self.assertIn("cuerpo con `y` y más", out)
        self.assertIn("tercera línea", out)
        self.assertEqual(out.count(":::"), 2)

    def test_callout(self):
        out = o.preprocess("> [!warning] Ojo\n> texto", {})
        self.assertIn(".callout-warning", out)
        self.assertIn("[Ojo]{.callout-title}", out)

    def test_imagen_no_encontrada(self):
        self.assertIn("imagen no encontrada", o.preprocess("![[x.png]]", {}))


class Labels(unittest.TestCase):
    def test_choque_de_nombres(self):
        got = dict(o.assign_labels([(Path("/a/acme/docs"), None), (Path("/b/beta/docs"), None)]))
        self.assertEqual(got[Path("/a/acme/docs")], "acme/docs")
        self.assertEqual(got[Path("/b/beta/docs")], "beta/docs")

    def test_nombre_explicito(self):
        self.assertEqual(o.assign_labels([(Path("/a/docs"), "X")]), [(Path("/a/docs"), "X")])


class Wikilinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = make_vault(Path(self.tmp.name), {
            "a.md": "---\nstatus: finished\n---\n",
            "b.md": "---\nstatus: finished\n---\n",
            "draft.md": "---\nstatus: draft\n---\n",
            "sub/b.md": "---\nstatus: finished\n---\n",
            "sub/c.md": "---\nstatus: finished\n---\n",
            "doc.pdf": "x",
        })
        self.links, _ = links_for(self.vault)
        self.a = self.vault / "a.md"

    def test_enlaza_al_pdf(self):
        self.assertEqual(self.links.resolve("b", self.a), self.vault / "b.pdf")

    def test_mismo_nombre_prefiere_misma_carpeta(self):
        cur = self.vault / "sub/c.md"
        self.assertEqual(self.links.resolve("b", cur), self.vault / "sub/b.pdf")

    def test_ruta_explicita(self):
        self.assertEqual(self.links.resolve("sub/c", self.a), self.vault / "sub/c.pdf")

    def test_ignora_seccion_y_extension(self):
        self.assertEqual(self.links.resolve("b#Sec", self.a), self.vault / "b.pdf")
        self.assertEqual(self.links.resolve("b.md", self.a), self.vault / "b.pdf")

    def test_texto_plano_si_no_procede(self):
        self.assertIsNone(self.links.resolve("draft", self.a))  # no finished
        self.assertIsNone(self.links.resolve("noexiste", self.a))
        self.assertIsNone(self.links.resolve("a", self.a))  # a sí misma
        self.assertIsNone(self.links.resolve("#Sec", self.a))

    def test_pdf_directo(self):
        self.assertEqual(self.links.resolve("doc.pdf", self.a), self.vault / "doc.pdf")

    def test_preprocess_genera_enlace(self):
        out = o.preprocess("ver [[b|la nota b]]", self.links.files, self.links, self.a)
        self.assertEqual(out, f"ver [la nota b]({(self.vault / 'b.pdf').resolve().as_uri()})")


class Embeds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = make_vault(Path(self.tmp.name), {
            "host.md": "---\nstatus: finished\n---\nhost",
            "inc.md": "---\nstatus: draft\n---\nTexto incluido\n",
            "sec.md": "# Uno\naaa\n## Sub\nsss\n# Dos\nbbb\n",
            "blk.md": "Primero\n\nEste bloque ^abc\n\nOtro\n",
            "loop1.md": "L1 ![[loop2]]",
            "loop2.md": "L2 ![[loop1]]",
            "code.md": "```\n[[NoTocar]]\n```\n",
        })
        self.links, _ = links_for(self.vault)
        self.host = self.vault / "host.md"

    def expand(self, text):
        return o.expand_embeds(text, self.links, self.host, (self.host,))

    def test_nota_completa_sin_frontmatter(self):
        out = self.expand("antes ![[inc]] despues")
        self.assertIn("::: {.embed}\nTexto incluido\n:::", out)
        self.assertNotIn("status", out)

    def test_seccion_incluye_subsecciones_y_corta_en_igual_nivel(self):
        out = self.expand("![[sec#Uno]]")
        self.assertIn("aaa", out)
        self.assertIn("sss", out)
        self.assertNotIn("bbb", out)
        self.assertIn("# Uno", out)

    def test_bloque(self):
        out = self.expand("![[blk#^abc]]")
        self.assertIn("Este bloque", out)
        self.assertNotIn("^abc", out)
        self.assertNotIn("Otro", out)

    def test_avisos(self):
        self.assertIn("nota no encontrada: nada", self.expand("![[nada]]"))
        self.assertIn("sección no encontrada", self.expand("![[sec#Nope]]"))

    def test_circular(self):
        self.assertIn("circular", self.expand("![[loop1]]"))

    def test_imagenes_y_codigo_intactos(self):
        self.assertEqual(self.expand("![[foto.png]]"), "![[foto.png]]")
        self.assertEqual(self.expand("```\n![[inc]]\n```"), "```\n![[inc]]\n```")

    def test_codigo_embebido_no_se_procesa(self):
        out = o.preprocess("![[code]]", {}, self.links, self.host)
        self.assertIn("[[NoTocar]]", out)

    def test_cambio_en_nota_incrustada_cambia_el_hash(self):
        cfg = o.load_config(Path(self.tmp.name) / "no.toml")
        (self.vault / "host.md").write_text("---\nstatus: finished\n---\n![[inc]]\n")
        state = {}
        with mock.patch.object(o, "build_pdf"):
            self.assertTrue(o.process_note(self.host, self.vault, "v", self.links, cfg, state, True))
            first = state[str(self.host)]
            (self.vault / "inc.md").write_text("---\n---\nOtro texto\n")
            self.assertTrue(o.process_note(self.host, self.vault, "v", self.links, cfg, state, True))
        self.assertNotEqual(first, state[str(self.host)])


class CodeAndCallouts(unittest.TestCase):
    def test_alias_de_lenguaje(self):
        out = o.normalize_fences("```shell\nx\n```\n~~~tsx\ny\n~~~\n```python\nz\n```")
        self.assertIn("```bash", out)
        self.assertIn("~~~typescript", out)
        self.assertIn("```python", out)

    def test_alias_no_toca_cierre_ni_contenido(self):
        src = "```\nshell\n```"
        self.assertEqual(o.normalize_fences(src), src)

    def test_highlight_args(self):
        with mock.patch.object(o, "pandoc_version", return_value=(3, 10)):
            self.assertEqual(o.highlight_args("kate"), ["--syntax-highlighting=kate"])
            self.assertEqual(o.highlight_args("none"), ["--syntax-highlighting=none"])
        with mock.patch.object(o, "pandoc_version", return_value=(3, 1)):
            self.assertEqual(o.highlight_args("kate"), ["--highlight-style=kate"])
            self.assertEqual(o.highlight_args("none"), ["--no-highlight"])

    def test_todos_los_callouts_tienen_estilo_e_icono(self):
        css = o.callout_css("#123456")
        for names, _, _, icon in o.CALLOUT_STYLES:
            self.assertIn(icon, o._ICONS)
            for n in names:
                self.assertIn(f".callout-{n} .callout-title::before", css)
        self.assertIn("#123456", css)  # el color primario se aplica a `note`

    def test_callout_con_guion(self):
        self.assertIn(".callout-mi-tipo", o.preprocess("> [!mi-tipo] T\n> x", {}))


class ForeignPdfAndWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = make_vault(Path(self.tmp.name), {"n.md": "---\nstatus: finished\n---\nhola\n"})
        self.note = self.vault / "n.md"
        self.cfg = o.load_config(Path(self.tmp.name) / "no.toml")
        self.cfg["vaults"] = [str(self.vault)]
        self.cfg["general"]["state_file"] = str(Path(self.tmp.name) / "state.json")
        self.links, _ = links_for(self.vault)

    def test_pdf_propio_se_reexporta_al_cambiar_la_nota(self):
        (self.vault / "n.pdf").write_bytes(b"%PDF-1.7 comprimido, sin marca")
        state = {str(self.note): "hash-antiguo"}  # ya la exportamos antes
        with mock.patch.object(o, "build_pdf") as build:
            self.assertTrue(o.process_note(self.note, self.vault, "v", self.links, self.cfg, state, False))
        build.assert_called_once()

    def test_pdf_ajeno_se_omite_y_avisa_una_sola_vez(self):
        (self.vault / "n.pdf").write_bytes(b"%PDF-1.7 de otro programa")
        state = {}
        with mock.patch.object(o, "build_pdf") as build, self.assertLogs("obsidian2pdf", "WARNING") as cm:
            for _ in range(3):
                self.assertFalse(o.process_note(self.note, self.vault, "v", self.links, self.cfg, state, False))
        build.assert_not_called()
        self.assertEqual(len(cm.records), 1)
        # si la nota cambia, vuelve a avisar
        self.note.write_text("---\nstatus: finished\n---\ncambiada\n")
        with self.assertLogs("obsidian2pdf", "WARNING"):
            o.process_note(self.note, self.vault, "v", self.links, self.cfg, state, False)

    def test_watch_no_reintenta_notas_fallidas_hasta_que_se_editan(self):
        failed = {}
        with mock.patch.object(o, "build_pdf", side_effect=o.ExportError("boom")) as build, \
                self.assertLogs("obsidian2pdf", "ERROR") as cm:
            for _ in range(3):
                o.export_vaults(self.cfg, {}, False, failed=failed)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(len(cm.records), 1)
        os_mtime = self.note.stat().st_mtime_ns + 10**9
        import os
        os.utime(self.note, ns=(os_mtime, os_mtime))  # editada: se reintenta
        with mock.patch.object(o, "build_pdf", side_effect=o.ExportError("boom")) as build, \
                self.assertLogs("obsidian2pdf", "ERROR"):
            o.export_vaults(self.cfg, {}, False, failed=failed)
        self.assertEqual(build.call_count, 1)

    def test_stop_interrumpe_la_pasada(self):
        stop = threading.Event()
        stop.set()
        with mock.patch.object(o, "build_pdf") as build:
            self.assertEqual(o.export_vaults(self.cfg, {}, False, stop=stop), (0, 0))
        build.assert_not_called()

    def test_intervalo_minimo(self):
        self.assertEqual(o.watch_interval({"general": {"watch_interval": 1}}), 5.0)
        self.assertEqual(o.watch_interval({"general": {"watch_interval": 30}}), 30.0)


class RelativeUri(unittest.TestCase):
    def test_relativo(self):
        self.assertEqual(o.relative_uri("file:///v/sub/b.pdf", Path("/v/sub")), "b.pdf")
        self.assertEqual(o.relative_uri("file:///v/a.pdf", Path("/v/sub")), "../a.pdf")
        self.assertEqual(o.relative_uri("file:///v/Mi%20nota.pdf", Path("/v")), "Mi%20nota.pdf")


class Pandoc(unittest.TestCase):
    def test_error_incluye_stderr(self):
        res = subprocess.CompletedProcess([], 64, stdout="", stderr="boom: plantilla rota")
        with mock.patch("subprocess.run", return_value=res):
            with self.assertRaisesRegex(o.ExportError, "boom: plantilla rota"):
                o.run_pandoc(["pandoc"], "x")

    def test_pandoc_ausente(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(o.ExportError, "pandoc"):
                o.run_pandoc(["pandoc"], "x")


class Dependencies(unittest.TestCase):
    cfg = {"branding": {"logo": ""}}

    def test_todo_ok(self):
        self.assertEqual(o.check_dependencies(self.cfg), [])

    def test_pandoc_antiguo_sin_extensiones(self):
        old = subprocess.CompletedProcess([], 0, stdout="+task_lists\n-smart\n", stderr="")
        with mock.patch("subprocess.run", return_value=old):
            self.assertEqual(o.missing_pandoc_extensions(), ["mark", "lists_without_preceding_blankline", "hard_line_breaks"])
            problems = o.check_dependencies(self.cfg)
        self.assertTrue(any("demasiado antiguo" in p and "mark" in p for p in problems))

    def test_pandoc_reciente_soporta_todo(self):
        self.assertEqual(o.missing_pandoc_extensions(), [])

    def test_sin_pandoc(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertTrue(any("pandoc" in p for p in o.check_dependencies(self.cfg)))


class EndToEnd(unittest.TestCase):
    def test_exporta_con_enlace_relativo(self):
        with tempfile.TemporaryDirectory() as d:
            vault = make_vault(Path(d), {
                "a.md": "---\nstatus: finished\n---\n# A\nVer [[b]].\n",
                "b.md": "---\nstatus: finished\n---\n# B\n",
            })
            cfg = o.load_config(Path(d) / "no.toml")
            links, _ = links_for(vault)
            state = {}
            self.assertTrue(o.process_note(vault / "a.md", vault, "v", links, cfg, state, True))
            self.assertTrue((vault / "a.pdf").read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
