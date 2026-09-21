#!/usr/bin/env bash
# Instalador interactivo de obsidian2pdf.
#
#   ./install.sh              instala (o actualiza) y pregunta la configuración
#   ./install.sh -y           instala con todos los valores por defecto, sin preguntar
#   ./install.sh --uninstall  desinstala el programa y el servicio
#
# Instala en:
#   ~/.local/share/obsidian2pdf/            programa, estilos y plantilla
#   ~/.local/bin/obsidian2pdf               comando
#   ~/.config/obsidian2pdf/config.toml      configuración
#   ~/.config/systemd/user/obsidian2pdf-watch.service   servicio (opcional)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/obsidian2pdf"
BIN="$HOME/.local/bin/obsidian2pdf"
CONF_DIR="$HOME/.config/obsidian2pdf"
CONF="$CONF_DIR/config.toml"
STATE="$HOME/.local/state/obsidian2pdf"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="obsidian2pdf-watch.service"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"  # sobrescribible, para pruebas

ASSUME_YES=0
MODE=install
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        --uninstall) MODE=uninstall ;;
        -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opción desconocida: $arg (prueba --help)" >&2; exit 2 ;;
    esac
done

# ---------------------------------------------------------------- utilidades

if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else B= G= Y= R= N=; fi
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%s✗%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
title() { printf '\n%s%s%s\n' "$B" "$*" "$N"; }

# ask VAR "Pregunta" "valor por defecto"  -> deja la respuesta en $VAR
ask() {
    local __var=$1 __q=$2 __def=${3-} __ans=
    if [ "$ASSUME_YES" -eq 0 ]; then
        read -r -p "$__q${__def:+ [$__def]}: " __ans || true
    fi
    printf -v "$__var" '%s' "${__ans:-$__def}"
}

# confirm "Pregunta" S|N  -> código 0 si la respuesta es sí (defecto según 2º argumento)
confirm() {
    local __q=$1 __def=${2:-S} __ans=
    if [ "$ASSUME_YES" -eq 0 ]; then
        read -r -p "$__q [$([ "$__def" = S ] && echo 'S/n' || echo 's/N')]: " __ans || true
    fi
    __ans=${__ans:-$__def}
    case "$__ans" in s|S|y|Y|si|SI|sí|Sí|yes) return 0 ;; *) return 1 ;; esac
}

# Cadena TOML entre comillas dobles, escapando \ y "
toml_str() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '"%s"' "$s"; }

valid_color() { [[ $1 =~ ^#[0-9a-fA-F]{6}$ ]]; }

# ---------------------------------------------------------------- desinstalar

if [ "$MODE" = uninstall ]; then
    title "Desinstalando obsidian2pdf"
    if command -v "$SYSTEMCTL" >/dev/null 2>&1; then
        "$SYSTEMCTL" --user disable --now "$UNIT" >/dev/null 2>&1 || true
    fi
    rm -f "$UNIT_DIR/$UNIT" "$BIN"
    rm -rf "$APP_DIR"
    command -v "$SYSTEMCTL" >/dev/null 2>&1 && "$SYSTEMCTL" --user daemon-reload >/dev/null 2>&1 || true
    ok "Programa, comando y servicio eliminados"
    if confirm "¿Borrar también la configuración y el estado ($CONF_DIR, $STATE)?" N; then
        rm -rf "$CONF_DIR" "$STATE"
        ok "Configuración y estado eliminados"
    else
        info "Se conservan la configuración y el estado."
    fi
    info "Los PDFs generados no se tocan."
    exit 0
fi

# ---------------------------------------------------------------- dependencias

title "1/5 Comprobando dependencias"
missing=()
pkgs=()
if ! command -v python3 >/dev/null 2>&1; then
    missing+=("python3"); pkgs+=(python3)
elif ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    missing+=("Python 3.11 o superior (tienes $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'))")
else
    python3 -c 'import yaml' 2>/dev/null || { missing+=("módulo Python PyYAML"); pkgs+=(yaml); }
    python3 -c 'import weasyprint' 2>/dev/null || { missing+=("módulo Python WeasyPrint"); pkgs+=(weasyprint); }
fi
command -v pandoc >/dev/null 2>&1 || { missing+=("pandoc"); pkgs+=(pandoc); }

if [ ${#missing[@]} -eq 0 ]; then
    ok "python3, PyYAML, WeasyPrint y pandoc disponibles"
else
    for m in "${missing[@]}"; do warn "Falta: $m"; done
    # Nombres de paquete orientativos según la distribución
    have() { local p; for p in "${pkgs[@]}"; do [ "$p" = "$1" ] && return 0; done; return 1; }
    names=()
    if command -v pacman >/dev/null 2>&1; then
        have pandoc && names+=(pandoc); have weasyprint && names+=(python-weasyprint); have yaml && names+=(python-yaml)
        hint="sudo pacman -S ${names[*]}"
    elif command -v apt-get >/dev/null 2>&1; then
        have pandoc && names+=(pandoc); have weasyprint && names+=(weasyprint); have yaml && names+=(python3-yaml)
        hint="sudo apt install ${names[*]}"
    elif command -v dnf >/dev/null 2>&1; then
        have pandoc && names+=(pandoc); have weasyprint && names+=(python3-weasyprint); have yaml && names+=(python3-pyyaml)
        hint="sudo dnf install ${names[*]}"
    else
        hint="instálalos con el gestor de tu distribución o con pip (pip install --user weasyprint pyyaml)"
    fi
    [ ${#names[@]} -gt 0 ] && info "Sugerencia (nombres de paquete orientativos): $hint" || info "Sugerencia: $hint"
    confirm "¿Continuar la instalación de todos modos?" S || die "Instalación cancelada. Instala las dependencias y vuelve a ejecutar ./install.sh"
fi

# ---------------------------------------------------------------- ficheros

title "2/5 Copiando el programa"
for f in obsidian2pdf.py style.css template.html; do
    [ -f "$SRC/$f" ] || die "No encuentro $f junto a install.sh ($SRC)"
done
mkdir -p "$APP_DIR" "$(dirname "$BIN")" "$CONF_DIR"
install -m 644 "$SRC/style.css" "$SRC/template.html" "$APP_DIR/"
install -m 755 "$SRC/obsidian2pdf.py" "$APP_DIR/"
ln -sf "$APP_DIR/obsidian2pdf.py" "$BIN"
ok "Programa instalado en $APP_DIR"
ok "Comando: $BIN"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "$HOME/.local/bin no está en tu PATH; añádelo para usar el comando 'obsidian2pdf'" ;; esac

# ---------------------------------------------------------------- configuración

title "3/5 Configuración"
write_config=1
if [ -f "$CONF" ]; then
    warn "Ya existe $CONF"
    if [ "$ASSUME_YES" -eq 1 ] || ! confirm "¿Sobrescribirla? (se guarda una copia como config.toml.bak)" N; then
        write_config=0
        info "Se conserva la configuración actual."
    else
        cp "$CONF" "$CONF.bak"
    fi
fi

if [ "$write_config" -eq 1 ]; then
    # --- bóvedas
    detected=()
    for j in "$HOME/.config/obsidian/obsidian.json" "$HOME/.var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json"; do
        [ -f "$j" ] || continue
        while IFS= read -r p; do [ -n "$p" ] && detected+=("$p"); done < <(python3 - "$j" <<'PY' 2>/dev/null || true
import json, sys
try:
    for v in (json.load(open(sys.argv[1])).get("vaults") or {}).values():
        if isinstance(v, dict) and v.get("path"):
            print(v["path"])
except Exception:
    pass
PY
)
    done
    auto_detect=false
    vaults=()
    if [ ${#detected[@]} -gt 0 ]; then
        info "Bóvedas registradas en Obsidian:"
        printf '  - %s\n' "${detected[@]}"
        confirm "¿Exportar todas ellas (y las que registres en el futuro)?" S && auto_detect=true
    fi
    if [ "$ASSUME_YES" -eq 0 ]; then
        info "Añade bóvedas por ruta (Enter vacío para terminar):"
        while :; do
            ask v "  Ruta de una bóveda" ""
            [ -z "$v" ] && break
            v=${v/#\~/$HOME}
            if [ -d "$v" ]; then vaults+=("$v"); else warn "  No existe el directorio: $v"; fi
        done
    fi
    if [ "$auto_detect" = false ] && [ ${#vaults[@]} -eq 0 ]; then
        warn "No has indicado ninguna bóveda: no se exportará nada hasta que las añadas en $CONF"
    fi

    # --- salida
    ask output_mode "¿Dónde guardar los PDFs? alongside = junto a cada nota, central = en una carpeta" "alongside"
    case "$output_mode" in alongside|central) ;; *) warn "Valor no válido, se usa 'alongside'"; output_mode=alongside ;; esac
    output_dir="~/Documentos/PDF"
    [ "$output_mode" = central ] && ask output_dir "  Carpeta de salida" "$output_dir"

    # --- datos del documento
    ask author "Autor por defecto (si la nota no trae 'author')" "${USER:-}"
    ask author_email "Email del autor (opcional)" ""
    ask company "Nombre de la empresa u organización" "Mi Empresa"

    # --- logo
    logo=""
    ask logo_src "Ruta a tu logo PNG/SVG (opcional, Enter para omitir)" ""
    if [ -n "$logo_src" ]; then
        logo_src=${logo_src/#\~/$HOME}
        if [ -f "$logo_src" ]; then
            ext=${logo_src##*.}
            cp "$logo_src" "$CONF_DIR/logo.$ext"
            logo="~/.config/obsidian2pdf/logo.$ext"
            ok "Logo copiado a $CONF_DIR/logo.$ext"
        else
            warn "No existe $logo_src: se continúa sin logo (el pie mostrará el texto de confidencialidad)"
        fi
    fi

    # --- colores
    ask primary "Color principal (hex)" "#1f3a5f"
    valid_color "$primary" || { warn "Color no válido, se usa #1f3a5f"; primary="#1f3a5f"; }
    ask accent "Color de acento (hex)" "#e07b00"
    valid_color "$accent" || { warn "Color no válido, se usa #e07b00"; accent="#e07b00"; }

    # --- idioma
    ask lang "Idioma de los textos del documento (es/en)" "es"
    if [ "$lang" = en ]; then
        toc_title="Contents"; page_label="Page"; confidential="Confidential document"; date_format="%Y-%m-%d"
        l_author="Author"; l_date="Date"; l_version="Version"; l_company="Organization"
    else
        [ "$lang" = es ] || warn "Idioma no reconocido, se usa 'es' (puedes editar los textos en $CONF)"
        lang=es
        toc_title="Índice"; page_label="Página"; confidential="Documento confidencial"; date_format="%d/%m/%Y"
        l_author="Autor"; l_date="Fecha"; l_version="Versión"; l_company="Organización"
    fi

    # --- escribir config.toml
    {
        echo "# ~/.config/obsidian2pdf/config.toml (generado por install.sh; edítalo cuando quieras)"
        echo "# Cada bóveda es una ruta o una tabla { path = \"...\", name = \"...\" }. El nombre solo"
        echo "# importa con output_mode = \"central\" (subcarpeta dentro de output_dir)."
        if [ ${#vaults[@]} -gt 0 ]; then
            echo "vaults = ["
            for v in "${vaults[@]}"; do echo "    $(toml_str "$v"),"; done
            echo "]"
        else
            echo "vaults = []"
        fi
        cat <<EOF

[general]
output_mode   = "$output_mode"            # alongside: el PDF junto a su .md | central: todo en output_dir
output_dir    = $(toml_str "$output_dir")     # solo se usa con output_mode = "central"
auto_detect_vaults = $auto_detect             # true: añade las bóvedas registradas en Obsidian
status_field  = "status"
export_status = "finished"
watch_interval = 30                     # segundos entre comprobaciones en modo --watch (mínimo 5)

[defaults]                              # se usan si la nota no trae author / author_email
author       = $(toml_str "$author")
author_email = $(toml_str "$author_email")

[branding]
company       = $(toml_str "$company")
logo          = $(toml_str "$logo")                      # ruta a PNG/SVG
primary_color = "$primary"
accent_color  = "$accent"
confidential  = $(toml_str "$confidential")     # pie izquierdo cuando NO hay logo
toc_title     = $(toml_str "$toc_title")
highlight_style = "pygments"            # pygments, tango, kate, monochrome, breezedark, zenburn... o none
page_label    = $(toml_str "$page_label")
date_format   = $(toml_str "$date_format")
lang          = "$lang"
justify       = true                    # texto justificado
hyphenate     = true                    # guiones automáticos al justificar
label_author  = $(toml_str "$l_author")                 # etiquetas de la portada
label_date    = $(toml_str "$l_date")
label_version = $(toml_str "$l_version")
label_company = $(toml_str "$l_company")
EOF
    } > "$CONF"
    # Comprobar que el TOML generado es válido
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import tomllib' 2>/dev/null; then
        python3 -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$CONF" \
            || die "El config.toml generado no es válido; revísalo: $CONF"
    fi
    ok "Configuración guardada en $CONF"
fi

# ---------------------------------------------------------------- servicio

title "4/5 Servicio en segundo plano"
if ! command -v "$SYSTEMCTL" >/dev/null 2>&1; then
    warn "systemd no disponible: no se instala el servicio. Ejecuta 'obsidian2pdf --watch' a mano si lo necesitas."
elif confirm "¿Instalar el servicio que vigila tus bóvedas y exporta al pasar una nota a 'finished'?" S; then
    [ -f "$SRC/systemd/$UNIT" ] || die "No encuentro systemd/$UNIT junto a install.sh"
    mkdir -p "$UNIT_DIR"
    py=$(command -v python3 || echo /usr/bin/python3)
    sed "s|^ExecStart=.*|ExecStart=$py $APP_DIR/obsidian2pdf.py --watch|" "$SRC/systemd/$UNIT" > "$UNIT_DIR/$UNIT"
    "$SYSTEMCTL" --user daemon-reload
    "$SYSTEMCTL" --user enable "$UNIT" >/dev/null 2>&1
    "$SYSTEMCTL" --user restart "$UNIT"  # restart: si ya estaba en marcha, carga la versión nueva
    ok "Servicio activo. Log: journalctl --user -u ${UNIT%.service} -f"
else
    info "Sin servicio. Puedes lanzarlo cuando quieras con: obsidian2pdf --watch"
fi

# ---------------------------------------------------------------- final

title "5/5 Listo"
info "Marca una nota con este frontmatter y se exportará a PDF:"
printf '\n  ---\n  status: finished\n  title: Mi documento\n  ---\n\n'
info "Comandos útiles:"
info "  obsidian2pdf                  exporta ahora todas las notas 'finished'"
info "  obsidian2pdf --file nota.md   exporta una nota concreta"
info "  ./install.sh --uninstall      desinstala"
