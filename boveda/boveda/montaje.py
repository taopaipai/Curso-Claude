"""Monta el video a partir del guion: voz sintetizada, subtitulos y fondo.

El guion que escribe Claude es texto para humanos, con sus bloques y sus
acotaciones. Aqui se convierte en una lista de escenas (lo que se dice, el
rotulo que se ve) y con eso se arma un mp4 vertical.

Piezas:
  1. desglosar()  el guion -> escenas, con Claude y salida estructurada
  2. sintetizar() cada escena -> un WAV de voz (piper local, un comando tuyo,
                  o silencio con la duracion estimada si no quieres voz)
  3. construir_ass() -> subtitulos quemados, repartidos por numero de caracteres
  4. montar()     -> ffmpeg junta fondo + voz + musica + subtitulos

Todo local: ffmpeg y el motor de voz corren en tu maquina. Anthropic no ofrece
sintesis de voz, asi que esa parte es tuya (piper es gratis y suena bien en
espanol).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import wave
from pathlib import Path
from typing import Any

from .config import Config
from .pipeline.analyze import _crear_mensaje, _texto_respuesta, cliente
from .pipeline.download import HerramientaFaltante

# Ritmo de lectura para estimar duraciones cuando no hay voz real.
CARACTERES_POR_SEGUNDO = 14.0
PAUSA_ENTRE_ESCENAS = 0.35
MAX_CARACTERES_SUBTITULO = 38

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "escenas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "voz": {"type": "string"},
                    "rotulo": {"type": "string"},
                    "nota_visual": {"type": "string"},
                },
                "required": ["voz", "rotulo", "nota_visual"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["escenas"],
    "additionalProperties": False,
}

INSTRUCCIONES = """Convierte este guion en una lista de escenas para montar un
video vertical corto.

Para cada escena:
- `voz`: EXACTAMENTE lo que se dice en voz alta, en texto limpio y locutable.
  Sin acotaciones, sin numeros de bloque, sin corchetes, sin marcas de tiempo,
  sin indicaciones de camara. Si una parte del guion no se dice (es una nota de
  produccion), no la incluyas aqui.
- `rotulo`: el texto corto que aparece en pantalla en esa escena, maximo 8
  palabras. Si el guion no propone ninguno, escribe una idea fuerza sacada de lo
  que se dice. Cadena vacia solo si de verdad no debe haber rotulo.
- `nota_visual`: que se ve, en una linea. Es documentacion para quien grabe;
  no aparece en el video.

Corta por unidades de sentido: cada escena es una frase o dos, lo que se dice de
un tiron. El gancho va siempre en la primera escena. No inventes contenido que
no este en el guion."""


class ErrorMontaje(RuntimeError):
    pass


# --- 1. guion -> escenas -----------------------------------------------------

def desglosar(cfg: Config, con: sqlite3.Connection, produccion_id: int,
              cli=None) -> list[dict[str, str]]:
    fila = con.execute(
        "SELECT formato, titulo, cuerpo FROM producciones WHERE id = ?", (produccion_id,)
    ).fetchone()
    if fila is None:
        raise ErrorMontaje(f"no existe la produccion {produccion_id}")

    contenido = (f"FORMATO: {fila['formato']}\nTITULO: {fila['titulo'] or ''}\n\n"
                 f"GUION:\n{fila['cuerpo']}")
    respuesta = _crear_mensaje(cli or cliente(), cfg, INSTRUCCIONES, contenido, ESQUEMA)
    escenas = json.loads(_texto_respuesta(respuesta)).get("escenas") or []
    if not escenas:
        raise ErrorMontaje("el desglose no devolvio ninguna escena")
    return escenas


# --- 2. voz ------------------------------------------------------------------

def duracion_wav(ruta: Path) -> float:
    with wave.open(str(ruta), "rb") as w:
        return w.getnframes() / float(w.getframerate() or 1)


def duracion_estimada(texto: str) -> float:
    return max(1.2, len(texto) / CARACTERES_POR_SEGUNDO)


def _silencio(ffmpeg: str, destino: Path, segundos: float) -> None:
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
         "-t", f"{segundos:.3f}", "-c:a", "pcm_s16le", str(destino)],
        check=True, capture_output=True,
    )


def sintetizar(cfg: Config, texto: str, destino: Path, ffmpeg: str = "ffmpeg") -> float:
    """Genera el WAV de una escena y devuelve su duracion en segundos."""
    crudo = destino.with_suffix(".crudo.wav")

    if cfg.motor_voz == "ninguna" or not texto.strip():
        _silencio(ffmpeg, destino, duracion_estimada(texto))
        return duracion_wav(destino)

    if cfg.motor_voz == "piper":
        if shutil.which("piper") is None:
            raise ErrorMontaje(
                "piper no esta instalado (pip install piper-tts y descarga una voz .onnx), "
                "o cambia BOVEDA_TTS_ENGINE a 'cmd' o 'ninguna'"
            )
        if not cfg.voz:
            raise ErrorMontaje("falta BOVEDA_TTS_VOICE con la ruta al modelo .onnx de piper")
        subprocess.run(
            ["piper", "--model", cfg.voz, "--output_file", str(crudo)],
            input=texto, text=True, check=True, capture_output=True,
        )
    elif cfg.motor_voz == "cmd":
        if not cfg.comando_voz:
            raise ErrorMontaje("BOVEDA_TTS_ENGINE=cmd pero BOVEDA_TTS_CMD esta vacio")
        import shlex
        comando = [p.replace("{texto}", texto).replace("{salida}", str(crudo))
                   for p in shlex.split(cfg.comando_voz)]
        subprocess.run(comando, check=True, capture_output=True)
        if not crudo.is_file():
            raise ErrorMontaje(f"el comando de voz no escribio el WAV en {crudo}")
    else:
        raise ErrorMontaje(f"motor de voz desconocido: {cfg.motor_voz}")

    # Se normaliza a 48 kHz mono para poder concatenar sin recodificar despues.
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(crudo),
         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(destino)],
        check=True, capture_output=True,
    )
    crudo.unlink(missing_ok=True)
    return duracion_wav(destino)


# --- 3. subtitulos -----------------------------------------------------------

def _tiempo_ass(segundos: float) -> str:
    segundos = max(0.0, segundos)
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    return f"{int(horas)}:{int(minutos):02d}:{seg:05.2f}"


def _escapar(texto: str) -> str:
    return texto.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def trocear_subtitulo(texto: str, limite: int = MAX_CARACTERES_SUBTITULO) -> list[str]:
    """Parte lo que se dice en trozos cortos, que es como se leen los subtitulos.

    Cada trozo cabe en una o dos lineas: el ajuste fino lo hace el renderizador
    de ASS (`WrapStyle: 0`), que sabe el ancho real de la fuente.
    """
    trozos: list[str] = []
    actual = ""
    for palabra in texto.split():
        if actual and len(actual) + 1 + len(palabra) > limite:
            trozos.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    if actual:
        trozos.append(actual)
    return trozos or [texto]


def construir_ass(escenas: list[dict[str, Any]], destino: Path, cfg: Config) -> None:
    """Escribe los subtitulos y los rotulos como un unico archivo ASS.

    Se usa ASS y no SRT porque hacen falta dos estilos a la vez: el rotulo grande
    arriba y el subtitulo abajo. Y se evita `drawtext`, cuyo escapado es una
    fuente inagotable de errores.
    """
    ancho, _, alto = cfg.resolucion.partition("x")
    cabecera = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ancho}
PlayResY: {alto}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Voz,{cfg.fuente},60,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,320,1
Style: Rotulo,{cfg.fuente},80,&H0000E5FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,8,80,80,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lineas: list[str] = []
    reloj = 0.0
    for escena in escenas:
        duracion = float(escena.get("duracion") or duracion_estimada(escena.get("voz", "")))
        fin_escena = reloj + duracion

        rotulo = _escapar((escena.get("rotulo") or "").strip())
        if rotulo:
            lineas.append(
                f"Dialogue: 0,{_tiempo_ass(reloj)},{_tiempo_ass(fin_escena)},"
                f"Rotulo,,0,0,0,,{rotulo}"
            )

        voz = (escena.get("voz") or "").strip()
        if voz:
            # Cada linea dura en proporcion a lo que se tarda en decirla.
            trozos = trocear_subtitulo(voz)
            total = sum(len(t) for t in trozos) or 1
            inicio = reloj
            for trozo in trozos:
                parte = duracion * (len(trozo) / total)
                lineas.append(
                    f"Dialogue: 1,{_tiempo_ass(inicio)},{_tiempo_ass(inicio + parte)},"
                    f"Voz,,0,0,0,,{_escapar(trozo)}"
                )
                inicio += parte

        reloj = fin_escena + PAUSA_ENTRE_ESCENAS

    destino.write_text(cabecera + "\n".join(lineas) + "\n", encoding="utf-8")


# --- 4. montaje --------------------------------------------------------------

def _ffmpeg() -> str:
    binario = shutil.which("ffmpeg")
    if binario is None:
        raise HerramientaFaltante(
            "Falta ffmpeg (brew install ffmpeg / sudo apt install ffmpeg)."
        )
    return binario


def _pista_de_voz(cfg: Config, escenas: list[dict[str, Any]], trabajo: Path,
                  ffmpeg: str) -> tuple[Path, float]:
    """Sintetiza cada escena, mete una pausa entre ellas y lo concatena todo."""
    piezas: list[Path] = []
    for indice, escena in enumerate(escenas):
        pieza = trabajo / f"voz_{indice:03d}.wav"
        escena["duracion"] = sintetizar(cfg, escena.get("voz", ""), pieza, ffmpeg)
        piezas.append(pieza)
        if indice < len(escenas) - 1:
            pausa = trabajo / f"pausa_{indice:03d}.wav"
            _silencio(ffmpeg, pausa, PAUSA_ENTRE_ESCENAS)
            piezas.append(pausa)

    lista = trabajo / "audio.txt"
    lista.write_text("".join(f"file '{p.name}'\n" for p in piezas), encoding="utf-8")
    voz = trabajo / "voz.wav"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", str(voz)],
        check=True, capture_output=True, cwd=trabajo,
    )
    return voz, duracion_wav(voz)


def _entrada_de_fondo(cfg: Config, fondo: Path | None, duracion: float) -> list[str]:
    if fondo is None:
        color = "0x0F172A"
        return ["-f", "lavfi", "-i",
                f"color=c={color}:s={cfg.resolucion}:r=30:d={duracion:.2f}"]
    if fondo.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return ["-loop", "1", "-t", f"{duracion:.2f}", "-i", str(fondo)]
    return ["-stream_loop", "-1", "-t", f"{duracion:.2f}", "-i", str(fondo)]


def montar(cfg: Config, con: sqlite3.Connection, produccion_id: int, *,
           fondo: Path | None = None, musica: Path | None = None,
           escenas: list[dict[str, Any]] | None = None, cli=None,
           rehacer: bool = False) -> dict[str, Any]:
    """Arma el video de una produccion y lo registra. Devuelve el resumen."""
    ffmpeg = _ffmpeg()

    guardado = con.execute(
        "SELECT escenas_json FROM montajes WHERE produccion_id = ?", (produccion_id,)
    ).fetchone()
    if escenas is None:
        # El desglose se reutiliza: cambiar fondo o voz no deberia costar otra
        # llamada al modelo.
        if guardado and not rehacer:
            escenas = json.loads(guardado["escenas_json"])
        else:
            escenas = desglosar(cfg, con, produccion_id, cli)

    trabajo = cfg.montajes / f"{produccion_id:05d}"
    if trabajo.exists():
        shutil.rmtree(trabajo)
    trabajo.mkdir(parents=True, exist_ok=True)

    voz, duracion = _pista_de_voz(cfg, escenas, trabajo, ffmpeg)
    subtitulos = trabajo / "subtitulos.ass"
    construir_ass(escenas, subtitulos, cfg)

    ancho, _, alto = cfg.resolucion.partition("x")
    cadena = (f"[0:v]scale={ancho}:{alto}:force_original_aspect_ratio=increase,"
              f"crop={ancho}:{alto},setsar=1,ass={subtitulos.name}[v]")
    entradas = _entrada_de_fondo(cfg, fondo, duracion) + ["-i", str(voz)]
    mapas = ["-map", "[v]", "-map", "1:a"]

    if musica:
        entradas += ["-stream_loop", "-1", "-t", f"{duracion:.2f}", "-i", str(musica)]
        cadena += ";[2:a]volume=0.12[m];[1:a][m]amix=inputs=2:duration=first[a]"
        mapas = ["-map", "[v]", "-map", "[a]"]

    salida = trabajo / "video.mp4"
    orden = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *entradas,
             "-filter_complex", cadena, *mapas,
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-r", "30",
             "-c:a", "aac", "-b:a", "192k", "-shortest", str(salida)]
    proceso = subprocess.run(orden, capture_output=True, text=True, cwd=trabajo)
    if proceso.returncode != 0 or not salida.is_file():
        detalle = (proceso.stderr or "").strip().splitlines()
        raise ErrorMontaje(f"ffmpeg fallo: {detalle[-1] if detalle else 'sin detalle'}")

    con.execute(
        """
        INSERT INTO montajes (produccion_id, ruta_video, ruta_audio, ruta_subtitulos,
                              duracion_seg, voz, escenas_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(produccion_id) DO UPDATE SET
            ruta_video = excluded.ruta_video, ruta_audio = excluded.ruta_audio,
            ruta_subtitulos = excluded.ruta_subtitulos,
            duracion_seg = excluded.duracion_seg, voz = excluded.voz,
            escenas_json = excluded.escenas_json, creado_en = datetime('now')
        """,
        (produccion_id, str(salida), str(voz), str(subtitulos), duracion,
         cfg.voz or cfg.motor_voz, json.dumps(escenas, ensure_ascii=False)),
    )
    con.commit()
    return {"video": salida, "duracion": duracion, "escenas": escenas,
            "subtitulos": subtitulos}


def video_de(con: sqlite3.Connection, produccion_id: int) -> Path | None:
    fila = con.execute(
        "SELECT ruta_video FROM montajes WHERE produccion_id = ?", (produccion_id,)
    ).fetchone()
    if fila and Path(fila["ruta_video"]).is_file():
        return Path(fila["ruta_video"])
    return None
