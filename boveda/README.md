# Bóveda

Archivo vivo de todo lo que guardaste en Instagram, TikTok y YouTube.

Cada guardado entra a una base de datos, se le baja el audio, se transcribe, se
desarma (gancho, estructura, por qué funciona, qué caducó y qué no) y queda
listo para convertirse en contenido tuyo: guion de reel, carrusel, hilo o
newsletter.

```
guardados → importar → descargar audio → transcribir ─┬─→ analizar → producir
                                                      │              ↓
                          videos sin voz → OCR de fotogramas    montar video
                                                      │       (voz + subtítulos)
                                                      │              ↓
                                    base de datos ←───┴──────→ publicar en redes
                                    búsqueda + export
```

## Por qué está hecho así

- **Todo en una carpeta.** Código, base de datos, audio, fotogramas y
  publicaciones viven bajo `boveda/`, y la ruta se ancla a la carpeta del
  proyecto: da igual desde dónde ejecutes el comando, los datos siempre caen en
  `boveda/data/`. Nada depende de un servicio que pueda cerrar; el día que
  quieras irte, te llevas esa carpeta.
- **Reanudable.** Cada item tiene un estado (`importado → descargado →
  transcrito → analizado`). Si algo falla a mitad de 3.000 videos, `boveda
  reintentar` retoma solo lo que quedó pendiente. Reimportar el mismo export no
  duplica nada: la clave es la URL canónica, sin parámetros de tracking.
- **El juicio de vigencia es parte del dato.** Es justo lo que preguntabas: cada
  análisis marca si el contenido está `vigente`, `caducado` o es `atemporal`, y
  le pone un `valor_historico` de 1 a 5 — cuánto vale conservarlo *aunque* el
  dato ya no aplique. Así puedes buscar "lo caducado pero valioso" como archivo
  histórico, y separarlo de lo que hoy sirve para publicar.
- **También lee lo que no se dice.** Media red social es texto quemado en
  pantalla sin una sola palabra hablada. Esos videos se pasan por OCR de
  fotogramas clave y entran a la bóveda con el mismo análisis que el resto.
- **Nada se publica solo sin que tú lo apruebes.** La publicación automática
  existe, pero pasa por dos puertas: aprobar la producción a mano y confirmar el
  envío. Sin las dos, todo es ensayo.
- **Sin inventar.** El prompt de análisis prohíbe rellenar huecos: si un dato no
  está en la transcripción, se queda vacío. Y toda afirmación fuerte se anota en
  `datos_y_afirmaciones` con si es verificable, para auditarla antes de reusarla.

## Instalación

```bash
cd boveda                          # todo el proyecto vive aquí dentro
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

`yt-dlp` se instala con el paquete. Para que el montaje hable, añade un motor de
voz (opcional; sin él, `boveda montar --sin-voz` funciona igual):

```bash
pip install piper-tts
# y descarga una voz, p. ej. es_ES-sharvard-medium.onnx, a la ruta que pongas
# en BOVEDA_TTS_VOICE
```

Configura tus claves:

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
boveda ocr                             # texto en pantalla de los videos sin voz
boveda analizar --limite 50            # Claude: gancho, estructura, vigencia
boveda reintentar                      # devuelve a la cola lo que falló
```

Empieza siempre con `--limite 10` en una plataforma para ver que las cookies y
el audio funcionan antes de lanzarte a miles de items.

### Videos de puro texto (OCR de fotogramas)

Un reel que solo muestra rótulos no tiene nada que transcribir. `boveda ocr`
saca los fotogramas clave con ffmpeg y lee el texto que aparece en pantalla:

```bash
boveda ocr                             # solo los que (casi) no tienen voz
boveda ocr --todos --limite 20         # también los hablados, para leer sus rótulos
boveda fotogramas 42                   # qué se leyó, segundo a segundo
```

Cómo elige los fotogramas: primero por cambio de plano, que es como cambian de
lámina estos videos. Si el video es un plano fijo con el texto apareciendo
encima, la detección de escena no encuentra nada y se cae a un muestreo uniforme
a lo largo del video. Máximo 12 fotogramas por pieza (`BOVEDA_OCR_MAX_FRAMES`).

Dos motores, en `BOVEDA_OCR_ENGINE`:

- `claude` (por defecto): lee tipografías de redes, emojis y texto sobre fondo
  con ruido, y de paso describe lo que se ve. Cuesta dinero por fotograma.
- `tesseract`: local y gratis, suficiente para rótulos limpios sobre fondo
  plano. Necesita el binario (`sudo apt install tesseract-ocr tesseract-ocr-spa`).

Al unificar el texto se saltan las repeticiones: una lámina que se mantiene tres
fotogramas se escribe una vez, y un rótulo que se va escribiendo solo aporta lo
nuevo. El resultado se lee como el guion de la pieza.

> **Ojo con el disco.** El OCR necesita imagen, así que si solo guardaste el
> audio (`BOVEDA_KEEP_VIDEO=0`, el valor por defecto) el video se descarga en
> ese momento y se queda en `media/`. Ve por lotes con `--limite`.

Por defecto solo procesa lo que tiene menos de 200 caracteres de transcripción
(`BOVEDA_OCR_UMBRAL`): hacer OCR de un video que ya trae tres minutos de voz es
gastar sin necesidad. `boveda estado` te dice cuántos hay pendientes.

Cuando la pieza no tiene voz, el análisis recibe el texto en pantalla con sus
tiempos y se le avisa de que todo el mensaje está ahí: el gancho y la estructura
se analizan sobre los rótulos y el ritmo con que aparecen. Y si un item no tiene
ni voz ni texto en pantalla, `analizar` lo marca con error **sin llamar a la
API**, en vez de pagar por analizar el vacío.

### Buscar

```bash
boveda buscar "gancho de curiosidad"
boveda buscar "email marketing"
boveda mostrar 42                      # el item completo en JSON
```

La búsqueda es full-text sobre título, transcripción, texto en pantalla **y**
análisis, sin acentos ni mayúsculas. Para consultas más finas, la base es SQLite normal:

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

### Montar el video (voz y subtítulos)

El guion es texto. `boveda montar` lo convierte en un mp4 vertical listo para
subir:

```bash
boveda montar 7                          # fondo de color plano
boveda montar 7 --fondo fondo.jpg        # imagen o video de fondo
boveda montar 7 --fondo broll.mp4 --musica pista.m4a
boveda montar 7 --sin-voz                # solo rótulos y subtítulos
```

Cuatro pasos, todos en tu máquina salvo el primero:

1. **Desglose.** Claude convierte el guion en escenas con salida estructurada:
   qué se dice exactamente (texto limpio y locutable, sin acotaciones) y qué
   rótulo aparece en pantalla. El desglose se guarda: cambiar el fondo o la voz
   y volver a montar **no** cuesta otra llamada al modelo, salvo `--rehacer`.
2. **Voz.** Se sintetiza cada escena por separado, lo que da la duración real de
   cada una — que es justo lo que hace falta para cuadrar los subtítulos.
3. **Subtítulos.** Un archivo ASS con dos estilos: el rótulo grande arriba y el
   subtítulo abajo, repartido en líneas cortas proporcionalmente a lo que se
   tarda en decir cada una.
4. **ffmpeg** junta fondo, voz, música y subtítulos quemados en un 1080×1920 a
   30 fps con audio AAC.

**La voz la pones tú**, en `BOVEDA_TTS_ENGINE`. Anthropic no ofrece síntesis de
voz, así que aquí no hay un motor "de la casa":

- `piper` (recomendado): local, gratis y con voces en español que suenan bien.
  `pip install piper-tts`, descarga un modelo `.onnx` y apúntalo con
  `BOVEDA_TTS_VOICE`.
- `cmd`: cualquier otro TTS (ElevenLabs, Azure, el que uses) a través de
  `BOVEDA_TTS_CMD`, con `{texto}` y `{salida}`. Solo tiene que escribir un WAV.
- `ninguna`: sin voz. Cada escena dura lo que se tarda en leerla y el video sale
  con rótulos y subtítulos sobre música. Útil para los formatos mudos, y es el
  modo con el que se prueban las piezas sin gastar nada.

El fondo horizontal se recorta a vertical automáticamente (`scale` + `crop`), y
un video de fondo más corto que la voz se repite hasta cubrirla.

**Se enlaza solo con la publicación:** si no pasas `--media`, `boveda publicar`
coge el video montado de esa producción.

```bash
boveda producir 42 --formato reel --nicho finanzas
boveda montar 8 --fondo marca.jpg
boveda aprobar 8
boveda publicar 8 --red tiktok --confirmar
```

### Publicar en nuestras redes

```bash
boveda redes --verificar                  # qué cuentas hay conectadas
boveda aprobar 7                          # tú das el visto bueno a la producción
boveda publicar 7 --red instagram --media-url https://cdn.tuweb.com/reel.mp4
#   ↑ esto es un ENSAYO: dice qué haría y no envía nada
boveda publicar 7 --red instagram --media-url https://... --confirmar
```

**Tres puertas antes de que algo salga a tu cuenta**, en este orden:

1. La producción tiene que estar en `aprobado`. Un borrador recién generado no
   se publica ni por error.
2. Sin `--confirmar` todo es ensayo: te dice a qué red iría, si faltan
   credenciales y si falta el medio, y no toca la API.
3. Una producción no sale dos veces en la misma red. No es una comprobación en
   el código, es un índice único en la base de datos.

Todo intento queda en la tabla `publicaciones` con su id remoto, su URL y el
error si lo hubo: `boveda publicaciones` es el historial.

**Programar y dejar que salga solo:**

```bash
boveda publicar 7 --red tiktok --media reel.mp4 --cuando 2026-09-10T18:00:00+00:00
boveda cola                               # ensayo: qué saldría ahora
boveda cola --confirmar                   # envía lo que ya toca
boveda cancelar 3                         # quita algo de la cola
```

Para que sea automático de verdad, una línea de cron con `cola --confirmar`:

```cron
*/15 * * * * cd /ruta/a/boveda && .venv/bin/boveda cola --confirmar >> data/cola.log 2>&1
```

**Qué necesita cada red** (son requisitos de cada plataforma, no de este código):

| Red | Formatos | Lo que te va a pedir |
|---|---|---|
| `instagram` | reel, carrusel, short | Cuenta profesional + página de Facebook, token con `instagram_business_content_publish`, y el medio en una **URL pública**: la API no acepta subidas directas. De ahí `BOVEDA_MEDIA_BASE_URL`. |
| `tiktok` | reel, short | Token con scope `video.publish`. **Hasta que TikTok audite tu app solo permite `SELF_ONLY`** (el video queda privado en tu cuenta), y ese es el valor por defecto aquí. |
| `youtube` | short, reel, blog | OAuth de aplicación instalada con `youtube.upload`; se guarda un refresh token una vez. Sube como `private` salvo que cambies `YT_PRIVACY`. |
| `x` | hilo | Token de usuario con escritura. El cuerpo se parte respetando los párrafos del guion y cada parte responde a la anterior. |
| `archivo` | todos | Ninguna credencial. Deja el texto y el medio en `data/publicaciones/`, listos para subir a mano. Es el destino por defecto para probar el flujo entero sin arriesgar nada. |

Los tokens van en tu `.env` (que está en `.gitignore`): mira `.env.example` para
los nombres exactos de cada variable.

> **Sobre las versiones de las APIs.** Los conectores siguen los flujos
> documentados de cada plataforma —contenedor + `media_publish` en Instagram,
> `video/init` + subida + `status/fetch` en TikTok, subida reanudable en
> YouTube—, pero **no están probados contra cuentas reales**: no tengo acceso a
> las tuyas. Estrena cada red con `--red archivo` primero, luego con la cuenta
> real y un contenido de prueba, y sabrás en un minuto si algún parámetro
> cambió. La versión de la API de Instagram se ajusta con
> `BOVEDA_IG_API_VERSION` sin tocar código.

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
se mueven en el rango de decenas de dólares. El OCR con `claude` cuesta aparte y
más, porque van 12 imágenes por pieza; si el archivo mudo es grande, empieza con
`BOVEDA_OCR_ENGINE=tesseract` y reserva el OCR con visión para lo que de verdad
vas a reutilizar. Es una estimación, no una factura:
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
      ocr.py              fotogramas clave con ffmpeg + texto en pantalla
      analyze.py          Claude + esquema JSON del análisis
      repurpose.py        generación de contenido nuevo
    prompts/              los prompts, en archivos aparte para que los edites
    export.py             markdown y json
    montaje.py            guion -> escenas -> voz + subtítulos + mp4
    publicador.py         cola, aprobaciones y registro de lo publicado
    publish/              un conector por red (+ base.py: HTTP y troceo de hilos)
  tests/                  55 pruebas, sin red (Claude y las APIs de redes simulados;
                          el montaje se verifica con ffmpeg de verdad)
  data/                   todo lo que genera el proyecto (fuera de git)
```

```bash
python -m pytest        # correr las pruebas
```

## Lo que todavía no hace

- El OCR lee fotogramas sueltos, no el video en movimiento: un texto que aparece
  y desaparece entre dos fotogramas clave se pierde. Sube
  `BOVEDA_OCR_MAX_FRAMES` en las piezas donde importe.
- El montaje no busca b-roll: el fondo es el que tú le pases. No hay banco de
  imágenes ni generación de vídeo.
- Los subtítulos se reparten por longitud de texto dentro de cada escena, no por
  alineación palabra a palabra con el audio. Cuadra bien porque cada escena se
  sintetiza por separado, pero no es karaoke.
- No detecta duplicados semánticos (el mismo consejo reempaquetado por diez
  cuentas). La dedup es por URL.
