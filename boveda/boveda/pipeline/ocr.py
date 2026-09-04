"""Lee el texto quemado en pantalla a partir de fotogramas clave.

Existe por los videos de puro texto: carruseles animados, reels sin voz, capturas
narradas con rotulos. Ahi la transcripcion sale vacia y, sin este paso, el item
no tiene nada que analizar.

Dos motores:
  claude    -> vision sobre los fotogramas; lee tipografias de redes, emojis y
               texto sobre fondo con ruido, y ademas describe lo que se ve
  tesseract -> OCR local y gratis; suficiente para rotulos limpios
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from ..config import Config
from .. import db
from . import download
from .analyze import _crear_mensaje, _texto_respuesta, cliente

RE_PTS = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")

# Umbral de deteccion de cambio de plano. Los videos de texto cambian de lamina
# de golpe, asi que un valor bajo captura cada lamina sin inundar de fotogramas.
ESCENA = 0.15

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fotogramas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indice": {"type": "integer"},
                    "texto_en_pantalla": {"type": "string"},
                    "descripcion_visual": {"type": "string"},
                },
                "required": ["indice", "texto_en_pantalla", "descripcion_visual"],
                "additionalProperties": False,
            },
        },
        "texto_unificado": {"type": "string"},
        "idioma": {"type": "string"},
    },
    "required": ["fotogramas", "texto_unificado", "idioma"],
    "additionalProperties": False,
}

INSTRUCCIONES = """Estas leyendo los fotogramas clave de un video corto de redes
sociales, en orden cronologico.

Para cada fotograma:
- `texto_en_pantalla`: transcribe LITERALMENTE todo el texto visible (rotulos,
  subtitulos quemados, texto de la interfaz, marcas de agua con el usuario).
  Respeta saltos de linea como espacios. Si no hay texto, cadena vacia.
- `descripcion_visual`: una linea sobre lo que se ve, solo si aporta contexto
  que el texto no da (quien aparece, que se muestra, que cambia).

`texto_unificado`: junta el texto de todos los fotogramas en orden de aparicion,
SIN repetir las partes que se mantienen identicas de un fotograma al siguiente
(un rotulo fijo se escribe una vez). El resultado debe leerse como el guion de
la pieza.

No interpretes ni resumas: transcribe. Si un texto esta cortado o ilegible,
escribe lo que se lea y marca lo demas con [...]."""


def comprobar_dependencias() -> None:
    if shutil.which("ffmpeg") is None:
        raise download.HerramientaFaltante(
            "Falta ffmpeg (brew install ffmpeg / sudo apt install ffmpeg)."
        )


def _ejecutar(cmd: list[str]) -> str:
    """Corre ffmpeg y devuelve stderr, que es donde escribe showinfo."""
    proceso = subprocess.run(cmd, capture_output=True, text=True)
    if proceso.returncode != 0 and "Output file" not in proceso.stderr:
        detalle = proceso.stderr.strip().splitlines()
        raise RuntimeError(detalle[-1] if detalle else "ffmpeg fallo")
    return proceso.stderr


def extraer_fotogramas(cfg: Config, video: Path, item_id: int,
                       duracion: float | None = None) -> list[tuple[int, float, Path]]:
    """Saca los fotogramas clave a disco y devuelve [(indice, segundo, ruta)].

    Primero por cambio de plano; si el video es un plano fijo con texto que
    aparece encima, eso no detecta nada y se cae a un muestreo uniforme.
    """
    destino = cfg.fotogramas / str(item_id)
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True, exist_ok=True)
    patron = str(destino / "f_%03d.jpg")

    salida = _ejecutar([
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(video),
        "-vf", f"select='eq(n\\,0)+gt(scene\\,{ESCENA})',scale=720:-2,showinfo",
        "-vsync", "vfr", "-frames:v", str(cfg.max_fotogramas), "-q:v", "4", patron,
    ])
    tiempos = [float(t) for t in RE_PTS.findall(salida)]
    rutas = sorted(destino.glob("f_*.jpg"))

    if len(rutas) < 2:
        for ruta in rutas:
            ruta.unlink()
        # Plano fijo: repartimos N capturas a lo largo del video.
        fps = (cfg.max_fotogramas / duracion) if duracion and duracion > 0 else 0.5
        salida = _ejecutar([
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(video),
            "-vf", f"fps={fps:.4f},scale=720:-2,showinfo",
            "-frames:v", str(cfg.max_fotogramas), "-q:v", "4", patron,
        ])
        tiempos = [float(t) for t in RE_PTS.findall(salida)]
        rutas = sorted(destino.glob("f_*.jpg"))

    return [
        (i, tiempos[i] if i < len(tiempos) else 0.0, ruta)
        for i, ruta in enumerate(rutas)
    ]


def _leer_con_claude(cfg: Config, fotogramas: list[tuple[int, float, Path]],
                     cli) -> tuple[str, list[dict[str, Any]]]:
    contenido: list[dict[str, Any]] = []
    for indice, segundo, ruta in fotogramas:
        contenido.append({"type": "text", "text": f"Fotograma {indice} — segundo {segundo:.1f}"})
        contenido.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(ruta.read_bytes()).decode("utf-8"),
            },
        })
    contenido.append({"type": "text", "text": "Lee estos fotogramas siguiendo las reglas dadas."})

    respuesta = _crear_mensaje(cli, cfg, INSTRUCCIONES, contenido, ESQUEMA)
    datos = json.loads(_texto_respuesta(respuesta))
    return datos.get("texto_unificado", ""), datos.get("fotogramas", [])


def _leer_con_tesseract(cfg: Config, fotogramas: list[tuple[int, float, Path]]
                        ) -> tuple[str, list[dict[str, Any]]]:
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "tesseract no esta instalado (brew install tesseract / "
            "sudo apt install tesseract-ocr tesseract-ocr-spa)"
        )
    leidos: list[dict[str, Any]] = []
    for indice, _segundo, ruta in fotogramas:
        proceso = subprocess.run(
            ["tesseract", str(ruta), "stdout", "-l", cfg.idiomas_ocr, "--psm", "6"],
            capture_output=True, text=True,
        )
        texto = " ".join(proceso.stdout.split())
        leidos.append({"indice": indice, "texto_en_pantalla": texto, "descripcion_visual": ""})
    return unificar(leidos), leidos


def unificar(leidos: list[dict[str, Any]]) -> str:
    """Encadena el texto de los fotogramas saltando lo que no cambia entre ellos."""
    partes: list[str] = []
    anterior = ""
    for entrada in leidos:
        texto = (entrada.get("texto_en_pantalla") or "").strip()
        if not texto or texto == anterior:
            continue
        # Un rotulo que crece (se va escribiendo) solo aporta lo nuevo.
        if anterior and texto.startswith(anterior):
            partes.append(texto[len(anterior):].strip())
        else:
            partes.append(texto)
        anterior = texto
    return "\n".join(p for p in partes if p)


def _asegurar_video(cfg: Config, con: sqlite3.Connection, item: sqlite3.Row) -> Path:
    """El OCR necesita imagen; si solo se guardo el audio, se baja el video ahora."""
    ruta = item["ruta_media"]
    if ruta and Path(ruta).is_file():
        return Path(ruta)
    video = download.descargar_video(cfg, item["url_canonica"], item["id"])
    if video is None:
        raise FileNotFoundError("no se pudo descargar el video para el OCR")
    con.execute("UPDATE items SET ruta_media = ? WHERE id = ?", (str(video), item["id"]))
    con.commit()
    return video


def procesar(cfg: Config, con: sqlite3.Connection, item: sqlite3.Row, cli=None) -> None:
    try:
        video = _asegurar_video(cfg, con, item)
        fotogramas = extraer_fotogramas(cfg, video, item["id"], item["duracion_seg"])
        if not fotogramas:
            raise RuntimeError("ffmpeg no extrajo ningun fotograma")

        if cfg.motor_ocr == "tesseract":
            texto, leidos = _leer_con_tesseract(cfg, fotogramas)
            motor = f"tesseract:{cfg.idiomas_ocr}"
        else:
            texto, leidos = _leer_con_claude(cfg, fotogramas, cli or cliente())
            motor = f"claude:{cfg.modelo}"
            if not texto:
                texto = unificar(leidos)

        guardar(con, item["id"], motor, texto, fotogramas, leidos)
        db.reindexar(con, item["id"])
    except Exception as exc:  # noqa: BLE001 - un item roto no debe parar el lote
        con.execute("UPDATE items SET error = ? WHERE id = ?", (f"ocr: {exc}", item["id"]))
        con.commit()
        raise


def guardar(con: sqlite3.Connection, item_id: int, motor: str, texto: str,
            fotogramas: list[tuple[int, float, Path]],
            leidos: list[dict[str, Any]]) -> None:
    por_indice = {int(d.get("indice", i)): d for i, d in enumerate(leidos)}
    con.execute(
        """
        INSERT INTO ocr (item_id, motor, texto, n_fotogramas) VALUES (?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            motor = excluded.motor, texto = excluded.texto,
            n_fotogramas = excluded.n_fotogramas, creado_en = datetime('now')
        """,
        (item_id, motor, texto, len(fotogramas)),
    )
    con.execute("DELETE FROM fotogramas WHERE item_id = ?", (item_id,))
    con.executemany(
        "INSERT INTO fotogramas (item_id, indice, segundo, ruta, texto, descripcion) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (item_id, indice, segundo, str(ruta),
             (por_indice.get(indice) or {}).get("texto_en_pantalla"),
             (por_indice.get(indice) or {}).get("descripcion_visual"))
            for indice, segundo, ruta in fotogramas
        ],
    )
    con.commit()


def candidatos(con: sqlite3.Connection, umbral: int, limite: int | None = None,
               todos: bool = False, plataforma: str | None = None) -> list[sqlite3.Row]:
    """Items ya descargados y sin OCR, cuya transcripcion no llega al umbral.

    Por defecto solo los mudos o casi mudos: hacer OCR de un video que ya trae
    tres minutos de voz transcrita es gastar sin necesidad.
    """
    sql = """
        SELECT i.* FROM items i
        LEFT JOIN transcripciones t ON t.item_id = i.id
        LEFT JOIN ocr o             ON o.item_id = i.id
        WHERE i.estado IN ('descargado', 'transcrito')
          AND o.item_id IS NULL
    """
    args: list[Any] = []
    if not todos:
        sql += " AND LENGTH(COALESCE(t.texto, '')) < ?"
        args.append(umbral)
    if plataforma:
        sql += " AND i.plataforma = ?"
        args.append(plataforma)
    sql += " ORDER BY i.id"
    if limite:
        sql += f" LIMIT {int(limite)}"
    return con.execute(sql, args).fetchall()
