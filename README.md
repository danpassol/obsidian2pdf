<div align="center">

# **obsidian2pdf**

**✦ dpastor.eu**

Exporta tus notas de Obsidian a PDF corporativo (portada, índice, cabecera y pie) cuando las marcas como `finished`.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![pandoc](https://img.shields.io/badge/pandoc-3.x-1f3a5f?style=flat-square)](https://pandoc.org)
[![WeasyPrint](https://img.shields.io/badge/WeasyPrint-PDF-e07b00?style=flat-square)](https://weasyprint.org)
[![Obsidian](https://img.shields.io/badge/Obsidian-compatible-7c3aed?style=flat-square&logo=obsidian&logoColor=white)](https://obsidian.md)
[![License](https://img.shields.io/badge/license-MIT-7c5cfc?style=flat-square)](LICENSE)

[🌐 dpastor.eu](https://dpastor.eu) · [📬 hola@dpastor.eu](mailto:hola@dpastor.eu) · [💼 LinkedIn](https://linkedin.com/in/danpassol) · [🐙 GitHub](https://github.com/danpassol)

</div>

## ¿Que hace el programa?

Como su nombre indica, exporta tus notas de obsidian a un PDF corporativo totalmente configurable.

Solo se exportan las notas con `status: finished` en el frontmatter, y solo se reexporta una nota cuando su contenido ha cambiado. El programa puede vigilar tus bóvedas en segundo plano con (`--watch`) y exportar en cuanto cambias el estado.

Flujo: frontmatter → ¿`status == finished`? → preprocesado de sintaxis Obsidian → pandoc (HTML) → WeasyPrint (PDF).

Mira los [ejemplos](#ejemplos) para ver el resultado.

## Ejemplos

Estos son tres documentos generados con la herramienta, para que veas el resultado antes de instalarla. Cada uno parte de una nota de Obsidian (la carpeta [`examples/`](examples/) incluye las tres notas y los PDFs). Usan una empresa y un autor ficticios.

GitHub muestra los PDFs directamente en el navegador al abrir el enlace.

| Ejemplo | Qué muestra |
|---|---|
| [Documentación técnica](examples/Ejemplo%20documentaci%C3%B3n%20t%C3%A9cnica.pdf) ([nota](examples/Ejemplo%20documentaci%C3%B3n%20t%C3%A9cnica.md)) | Portada, índice, tablas, imagen con ancho, listas de tareas, glosario, enlaces entre PDFs y una sección incrustada de otra nota. |
| [Documentación de una app](examples/Ejemplo%20documentaci%C3%B3n%20de%20app.pdf) ([nota](examples/Ejemplo%20documentaci%C3%B3n%20de%20app.md)) | Bloques de código resaltados en bash, YAML, JSON, Python, TypeScript, SQL y Dockerfile, y ajuste de líneas largas. |
| [Despliegue de Jenkins en Docker](examples/Ejemplo%20despliegue%20jenkins%20en%20docker.pdf) ([nota](examples/Ejemplo%20despliegue%20jenkins%20en%20docker.md)) | Callouts de 13 tipos, cada uno con su color e icono. |

## Requisitos

- Python 3.11 o superior (usa `tomllib`)
- [pandoc](https://pandoc.org/) 3.x (probado con 3.10). Al arrancar se comprueba que soporte las extensiones que se usan y se avisa si es demasiado antiguo. Algunas distribuciones traen versiones viejas; en ese caso descarga una reciente desde [pandoc.org](https://pandoc.org/installing.html)
- [WeasyPrint](https://weasyprint.org/) y PyYAML
- Fuentes: por defecto Liberation Sans o DejaVu Sans

En Arch Linux:

```bash
sudo pacman -S pandoc python-weasyprint python-yaml
```

Al arrancar, el programa comprueba las dependencias y avisa de lo que falte. El instalador exige Python 3.11 o superior y se detiene si tu `python3` es más antiguo.

## Instalación

```bash
./install.sh
```

El instalador:

1. Comprueba las dependencias y, si falta alguna, te dice el comando para instalarla. No instala paquetes por su cuenta.
2. Copia el programa a `~/.local/share/obsidian2pdf/` y crea el comando `obsidian2pdf` en `~/.local/bin/`.
3. Te pregunta la configuración y genera `~/.config/obsidian2pdf/config.toml`. Todas las preguntas tienen un valor por defecto, así que basta con pulsar Enter:
   - bóvedas (detecta las que Obsidian tiene registradas, en su versión nativa, Flatpak o Snap, y te deja añadir más por ruta),
   - dónde guardar los PDFs, autor y email, empresa, logo, colores e idioma (`es` o `en`).
4. Ofrece instalar el servicio en segundo plano (ver [Ejecutar como servicio](#ejecutar-como-servicio-systemd-de-usuario)). Si tu sistema no tiene sesión de systemd de usuario (WSL, contenedores, distros sin systemd), lo omite y sigue; puedes usar `obsidian2pdf --watch` a mano.

Otras opciones:

```bash
./install.sh -y          # todo por defecto, sin preguntar (no sobrescribe una configuración existente)
./install.sh --uninstall # desinstala el programa y el servicio
```

Se puede volver a ejecutar para actualizar: copia la versión nueva y, si ya hay configuración, pregunta antes de tocarla (y guarda una copia `config.toml.bak` si la sobrescribes). La desinstalación pregunta si quieres borrar también la configuración y el estado, y nunca borra los PDFs generados.

### Instalación manual

```bash
mkdir -p ~/.local/share/obsidian2pdf ~/.config/obsidian2pdf
cp obsidian2pdf.py style.css template.html ~/.local/share/obsidian2pdf/
cp config.example.toml ~/.config/obsidian2pdf/config.toml
```

Edita `~/.config/obsidian2pdf/config.toml` y añade tus bóvedas en `vaults`.

## Uso

Tras la instalación tienes el comando `obsidian2pdf` (en `~/.local/bin`, que debe estar en tu `PATH`):

```bash
obsidian2pdf                  # una pasada por todas las bóvedas
obsidian2pdf --watch          # no sale: vigila y exporta al detectar cambios
obsidian2pdf --file nota.md   # exporta una nota concreta (ignora el status)
obsidian2pdf --force          # reexporta aunque no haya cambios
obsidian2pdf --config ruta.toml
obsidian2pdf -v               # incluye el log detallado de WeasyPrint
```

Si no lo has instalado con `install.sh`, ejecuta el script directamente: `python3 obsidian2pdf.py [opciones]`.

En cada nota, el frontmatter controla la exportación y la portada:

```yaml
---
status: finished      # solo se exportan las notas con este valor
title: Mi documento   # por defecto, el nombre del fichero
subtitle: Opcional
author: Nombre        # por defecto, [defaults] author
author_email: yo@ejemplo.com
date: 2026-09-21      # por defecto, la fecha de modificación
version: "1.0"
---
```

## Configuración

Ver `config.example.toml`, que documenta cada opción.

- **`[general]`**: dónde se guardan los PDFs (`alongside` junto a la nota o `central` en `output_dir`), el campo y el valor de estado (`status_field`, `export_status`), `auto_detect_vaults` para usar las bóvedas registradas en Obsidian, y `watch_interval` para `--watch`. También admite `exclude_dirs` y `state_file`.
- **`[defaults]`**: autor y email si la nota no los trae.
- **`[branding]`**: empresa, logo, colores, fuente, textos de la portada, del índice y del pie, formato de fecha, idioma, justificado y estilo de resaltado del código.

Todo es opcional. Lo que no definas usa el valor por defecto, en castellano.

**Logo:** aparece en la portada y en el pie de cada página. Sin logo, el pie muestra el texto de `confidential`.

**Estado:** el hash de cada nota exportada se guarda en `~/.local/state/obsidian2pdf/state.json`. Un PDF que ya existe pero cuya nota no consta en el estado se considera ajeno y no se sobrescribe. Se avisa una sola vez por versión de la nota.

## Sintaxis de Obsidian soportada

| Sintaxis | Resultado |
|---|---|
| `[[Nota]]`, `[[Nota\|alias]]` | Enlace relativo al PDF de esa nota, si existe en la bóveda y es `finished`. Si no, texto plano. Apunta al fichero, no a la sección. |
| `[[Documento.pdf]]` | Enlace relativo a ese fichero. |
| `![[Nota]]`, `![[Nota#Sección]]`, `![[Nota#^bloque]]` | Contenido de la nota, de la sección o del bloque. Admite embeds anidados con detección de ciclos. |
| `![[imagen.png\|300]]` | Imagen, con ancho opcional en píxeles. |
| `> [!tipo] Título` | Callout con color e icono. Están los 28 tipos y alias de Obsidian. Los tipos desconocidos usan el estilo de `note`. |
| `%% comentario %%` | Se elimina. |
| ` ```lenguaje ` | Código resaltado (~140 lenguajes). |
| `==texto==`, `- [ ]`, `$math$` | Resaltado, listas de tareas y fórmulas. |

Los alias de lenguaje habituales se traducen: `shell` y `console` a bash, `tsx` a typescript, `golang` a go, `hcl` a terraform, `jsonc` a json, `docker` a dockerfile y `vue` o `svelte` a html.

**Limitaciones conocidas:**

- Los callouts anidados no están soportados.
- Los títulos de una nota incrustada mantienen su nivel original.
- Si una nota enlazada pasa a `finished` después, la que la enlaza no se reexporta sola; hace falta `--force`.

## Ejecutar como servicio (systemd de usuario)

El instalador puede hacerlo por ti. Para montarlo a mano:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now obsidian2pdf-watch.service
journalctl --user -u obsidian2pdf-watch -f
```

`obsidian2pdf-watch.service` mantiene el programa en marcha con `--watch`. Con la bóveda quieta no escribe nada en el log.

En modo watch, una nota que falla se registra una sola vez y no se reintenta hasta que se edite. Si editas `config.toml`, se recarga sola.

`obsidian2pdf.service` es una pasada única, para lanzarla a mano con `systemctl --user start obsidian2pdf.service`.

## Personalizar el diseño

- `style.css` define la página, la portada, el índice, las tablas, el código y los callouts.
- `template.html` es la plantilla de pandoc de la portada.

Los valores `${...}` de `style.css` los rellena el programa desde la configuración.

## Tests

```bash
python3 -m unittest discover -s tests
```

Solo usan la biblioteca estándar. Algunos ejecutan pandoc y WeasyPrint de verdad.

## Licencia

Distribuido bajo la licencia MIT. Consulta el fichero [LICENSE](LICENSE) para más información.
