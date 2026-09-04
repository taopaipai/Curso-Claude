"""Descarga metadatos, audio y (opcionalmente) video con yt-dlp.

El audio es lo unico imprescindible: es la entrada del transcriptor y pesa
dos ordenes de magnitud menos que el video. `BOVEDA_KEEP_VIDEO=1` guarda el mp4
cuando ademas quieres el material en bruto para re-editar.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from ..config import Config
from .. import db


class HerramientaFaltante(RuntimeError):
    pass


def comprobar_dependencias() -> None:
    faltan = [b for b in ("yt-dlp", "ffmpeg") if shutil.which(b) is None]
    if faltan:
        raise HerramientaFaltante(
            "Faltan herramientas: " + ", ".join(faltan) +
            ". Instala con: pip install yt-dlp  y  ffmpeg (brew/apt install ffmpeg)."
        )


def _opciones_comunes(cfg: Config) -> list[str]:
    opciones = ["--no-warnings", "--ignore-config"]
    if cfg.cookies and cfg.cookies.is_file():
        opciones += ["--cookies", str(cfg.cookies)]
    return opciones


def metadatos(cfg: Config, url: str) -> dict[str, Any]:
    salida = subprocess.run(
        ["yt-dlp", *_opciones_comunes(cfg), "--dump-single-json", "--skip-download", url],
        capture_output=True, text=True, check=True,
    )
    return json.loads(salida.stdout)


def descargar_audio(cfg: Config, url: str, item_id: int) -> Path:
    destino = cfg.audio / f"{item_id}.%(ext)s"
    subprocess.run(
        ["yt-dlp", *_opciones_comunes(cfg),
         "-x", "--audio-format", "m4a", "--audio-quality", "0",
         "-o", str(destino), url],
        capture_output=True, text=True, check=True,
    )
    esperado = cfg.audio / f"{item_id}.m4a"
    if not esperado.is_file():
        encontrados = sorted(cfg.audio.glob(f"{item_id}.*"))
        if not encontrados:
            raise FileNotFoundError(f"yt-dlp no dejo audio para el item {item_id}")
        return encontrados[0]
    return esperado


def descargar_video(cfg: Config, url: str, item_id: int) -> Path | None:
    destino = cfg.media / f"{item_id}.%(ext)s"
    subprocess.run(
        ["yt-dlp", *_opciones_comunes(cfg),
         "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", str(destino), url],
        capture_output=True, text=True, check=True,
    )
    encontrados = sorted(cfg.media.glob(f"{item_id}.*"))
    return encontrados[0] if encontrados else None


def _metricas(info: dict[str, Any]) -> str:
    campos = ("view_count", "like_count", "comment_count", "repost_count",
              "channel_follower_count", "average_rating")
    return json.dumps({c: info.get(c) for c in campos if info.get(c) is not None})


def procesar(cfg: Config, con: sqlite3.Connection, item: sqlite3.Row) -> None:
    """Descarga un item y actualiza su fila. Los errores quedan en `items.error`."""
    url = item["url_canonica"]
    try:
        info = metadatos(cfg, url)
        audio = descargar_audio(cfg, url, item["id"])
        video = descargar_video(cfg, url, item["id"]) if cfg.guardar_video else None

        fecha = info.get("upload_date")  # YYYYMMDD
        publicado = f"{fecha[:4]}-{fecha[4:6]}-{fecha[6:]}" if fecha and len(fecha) == 8 else None

        con.execute(
            """
            UPDATE items SET
                autor = COALESCE(?, autor),
                titulo = COALESCE(?, titulo),
                descripcion = COALESCE(?, descripcion),
                duracion_seg = COALESCE(?, duracion_seg),
                publicado_en = COALESCE(?, publicado_en),
                idioma = COALESCE(?, idioma),
                metricas_json = ?,
                ruta_audio = ?,
                ruta_media = ?
            WHERE id = ?
            """,
            (
                info.get("uploader") or info.get("channel") or info.get("uploader_id"),
                info.get("title"),
                info.get("description"),
                int(info["duration"]) if info.get("duration") else None,
                publicado,
                info.get("language"),
                _metricas(info),
                str(audio),
                str(video) if video else None,
                item["id"],
            ),
        )
        db.marcar(con, item["id"], "descargado", None)
        db.reindexar(con, item["id"])
    except subprocess.CalledProcessError as exc:
        detalle = (exc.stderr or "").strip().splitlines()
        db.marcar(con, item["id"], "error_descarga", detalle[-1] if detalle else str(exc))
    except Exception as exc:  # noqa: BLE001 - un item roto no debe parar el lote
        db.marcar(con, item["id"], "error_descarga", str(exc))
