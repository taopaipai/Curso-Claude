# Bóveda

Archivo vivo de todo lo que guardaste en Instagram, TikTok y YouTube.

Cada guardado entra a una base de datos, se le baja el audio, se transcribe, se
desarma (gancho, estructura, por qué funciona, qué caducó y qué no) y queda
listo para convertirse en contenido tuyo: guion de reel, carrusel, hilo o
newsletter.

```
guardados → importar → descargar audio → transcribir → analizar → producir
                                                         ↓
                                            base de datos + búsqueda + export
```

## Por qué está hecho así

- **Todo local.** SQLite + archivos en tu disco. Nada depende de un servicio que
  pueda cerrar; el día que quieras irte, te llevas una carpeta.
- **Reanudable.** Cada item tiene un estado (`importado → descargado →
  transcrito → analizado`). Si algo falla a mitad de 3.000 videos, `boveda
  reintentar` retoma solo lo que quedó pendiente. Reimportar el mismo export no
  duplica nada: la clave es la URL canónica, sin parámetros de tracking.
- **El juicio de vigencia es parte del dato.** Es justo lo que preguntabas: cada
  análisis marca si el contenido está `vigente`, `caducado` o es `atemporal`, y
  le pone un `valor_historico` de 1 a 5 — cuánto vale conservarlo *aunque* el
  dato ya no aplique. Así puedes buscar "lo caducado pero valioso" como archivo
  histórico, y separarlo de lo que hoy sirve para publicar.
- **Sin inventar.** El prompt de análisis prohíbe rellenar huecos: si un dato no
  está en la transcripción, se queda vacío. Y toda afirmación fuerte se anota en
  `datos_y_afirmaciones` con si es verificable, para auditarla antes de reusarla.

## Instalación

```bash
cd boveda
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[whisper]"        # sin [whisper] si vas a transcribir por otra vía
```

Necesitas además dos binarios del sistema:

```bash
# macOS
brew install ffmpeg
# Debian/Ubuntu
sudo apt install ffmpeg
```

`yt-dlp` se instala con el paquete. Configura tus claves:

```bash
cp .env.example .env
# edita .env: ANTHROPIC_API_KEY y, si hace falta, BOVEDA_COOKIES_FILE
boveda init
```

## Cómo sacar tus guardados de cada plataforma

Ninguna de las tres tiene API pública de guardados. La vía estable —y la única
que no te arriesga a un bloqueo de cuenta— es el export oficial de datos:

**Instagram** → Ajustes → *Tu actividad* → *Descargar tu información* → formato
**JSON**. En el ZIP busca `your_instagram_activity/saved/saved_posts.json` (y
`saved_collections.json` si organizaste por colecciones: de ahí sale el nombre
de cada carpeta).

```bash
boveda importar instagram ~/Downloads/ig/saved_posts.json
boveda importar instagram ~/Downloads/ig/saved_collections.json
```

**TikTok** → Ajustes → *Configuración de la cuenta* → *Descargar tus datos* →
formato **JSON**. Llega un `user_data.json` con favoritos, me gusta e historial;
el importador los reconoce y los mete en carpetas separadas.

```bash
boveda importar tiktok ~/Downloads/tiktok/user_data.json
```

**YouTube** → [Google Takeout](https://takeout.google.com) → *YouTube y YouTube
Music* → solo *playlists*. Sale un CSV por lista (incluidas "Ver más tarde" y
"Me gusta"). O directamente por URL de playlist, sin Takeout:

```bash
boveda importar youtube ~/Takeout/YouTube/playlists/Ver\ más\ tarde-videos.csv
boveda importar youtube "https://www.youtube.com/playlist?list=PL..." --carpeta tutoriales
```

**Cualquier otra cosa** (enlaces sueltos, lo que te pasan por WhatsApp):

```bash
boveda importar urls enlaces.txt
```

```
# copywriting          <- las líneas siguientes van a esta carpeta
https://www.tiktok.com/@alguien/video/123
https://youtu.be/abcdefghijk, storytelling    <- o carpeta por línea
```

> **Guardados privados.** Para bajar reels o TikToks que solo se ven con sesión
> iniciada, exporta las cookies de tu navegador a un `cookies.txt` y apunta
> `BOVEDA_COOKIES_FILE` a él. Baja siempre solo tu propio material guardado y a
> un ritmo razonable (`--limite`): esto es un archivo personal, no un scraper.

## El flujo

```bash
boveda estado                          # qué hay y en qué etapa está
boveda descargar --limite 50           # metadatos + audio (y video si KEEP_VIDEO=1)
boveda transcribir                     # whisper local, sin coste por minuto
boveda analizar --limite 50            # Claude: gancho, estructura, vigencia
boveda reintentar                      # devuelve a la cola lo que falló
```

Empieza siempre con `--limite 10` en una plataforma para ver que las cookies y
el audio funcionan antes de lanzarte a miles de items.

### Buscar

```bash
boveda buscar "gancho de curiosidad"
boveda buscar "email marketing"
boveda mostrar 42                      # el item completo en JSON
```

La búsqueda es full-text sobre título, transcripción **y** análisis, sin
acentos ni mayúsculas. Para consultas más finas, la base es SQLite normal:

```sql
-- lo caducado que aun así vale la pena conservar
SELECT i.url_canonica, a.tema_principal, a.valor_historico
FROM analisis a JOIN items i ON i.id = a.item_id
WHERE a.vigencia_estado = 'caducado' AND a.valor_historico >= 4;

-- inventario de ganchos por técnica
SELECT hook_tecnica, COUNT(*) n FROM analisis GROUP BY 1 ORDER BY n DESC;
```

### Producir contenido nuestro

```bash
boveda producir 42 --formato carrusel --nicho finanzas
boveda producir 42 --formato reel --notas "tono directo, sin intro"
```

Formatos: `reel`, `short`, `carrusel`, `hilo`, `newsletter`, `blog`. El
guionista trabaja sobre el **análisis**, no sobre el texto original: reutiliza
la estructura y el mecanismo, nunca las frases, y descarta los datos que el
análisis marcó como caducados o no verificables. Cada producción queda guardada
en la tabla `producciones` con estado `borrador`.

### Exportar

```bash
boveda exportar --formato md           # una nota por item, con frontmatter (Obsidian/Notion)
boveda exportar --formato json
```

## Qué guarda el análisis

Cada item analizado produce un JSON validado contra esquema (structured outputs,
así que siempre parsea):

| Campo | Para qué sirve |
|---|---|
| `hook` | texto literal, técnica, en qué segundo, por qué funciona |
| `estructura` | recorrido real de la pieza con timestamps y propósito de cada bloque |
| `por_que_funciona` | factores, emoción dominante, promesa, tensión, resolución |
| `ganchos_reutilizables` | plantillas de gancho extraídas, listas para adaptar |
| `datos_y_afirmaciones` | cada afirmación fuerte + si es verificable + contexto temporal |
| `vigencia` | `vigente`/`caducado`/`atemporal`, la razón y el valor histórico 1-5 |
| `aplicabilidad` | qué hacer nosotros, qué enseñar, en qué formatos |

## Coste aproximado

La transcripción es gratis (whisper corre en tu máquina; un video de 1 minuto
tarda unos segundos con el modelo `medium` en CPU).

El análisis con `claude-opus-5` sale, en orden de magnitud, a **unos pocos
céntimos por video** de un minuto (entrada ~2K tokens, salida ~1,5K). Mil videos
se mueven en el rango de decenas de dólares. Es una estimación, no una factura:
mide con `boveda analizar --limite 20` y extrapola. Si quieres bajarlo, cambia
`BOVEDA_MODEL=claude-sonnet-5` en el `.env` para el grueso del archivo y deja
Opus para lo que de verdad vas a publicar.

## Estructura

```
boveda/
  schema.sql              esquema SQLite comentado
  boveda/
    config.py             .env y rutas
    db.py                 conexión, dedup, índice FTS5
    cli.py                todos los comandos
    ingest/               un importador por plataforma (+ base.py: URLs canónicas)
    pipeline/
      download.py         yt-dlp: metadatos, audio, video opcional
      transcribe.py       faster-whisper local, o un comando externo tuyo
      analyze.py          Claude + esquema JSON del análisis
      repurpose.py        generación de contenido nuevo
    prompts/              los prompts, en archivos aparte para que los edites
    export.py             markdown y json
  tests/                  13 pruebas, sin red (cliente de Claude simulado)
```

```bash
python -m pytest        # correr las pruebas
```

## Lo que todavía no hace

- No lee texto en pantalla (OCR de los subtítulos quemados en el video). Para
  piezas mudas o de puro texto, la transcripción queda vacía y el item se marca
  con error; ahí hace falta un paso de visión sobre fotogramas.
- No publica solo: deja borradores en `producciones`. La conexión a la API de
  publicación de cada red es el siguiente paso natural.
- No detecta duplicados semánticos (el mismo consejo reempaquetado por diez
  cuentas). La dedup es por URL.
