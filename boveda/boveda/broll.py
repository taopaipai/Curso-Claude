"""Busca y cachea clips de b-roll para el fondo del video.

Cuatro origenes, en orden de preferencia:
  local     una carpeta tuya de clips; el nombre del archivo hace de etiqueta
  pexels    banco de stock gratuito (necesita PEXELS_API_KEY)
  pixabay   banco de stock gratuito (necesita PIXABAY_API_KEY)
  generado  degradado en movimiento hecho con ffmpeg; siempre funciona

Lo descargado se guarda en `data/broll/` con el nombre del proveedor y el id,
asi que remontar el mismo guion no vuelve a bajar nada.

Ojo con lo obvio: NUNCA se usa como b-roll el video original guardado. Eso es
material de otro y reutilizarlo es justo lo que este proyecto no hace.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import web
from .config import Config

PALABRAS_VACIAS = {
    "de", "la", "el", "los", "las", "un", "una", "y", "o", "que", "en", "con",
    "para", "por", "the", "a", "of", "and", "to", "in", "on", "plano", "primer",
    "imagen", "video", "toma",
}


@dataclass
class Clip:
    ruta: Path
    origen: str
    autor: str = ""
    url: str = ""
    consulta: str = ""

    def credito(self) -> str:
        if self.origen in ("generado", "local"):
            return ""
        return f"«{self.consulta}» — {self.autor or 'autor desconocido'} ({self.origen}) {self.url}".strip()


class ErrorBroll(RuntimeError):
    pass


def terminos(*textos: str, maximo: int = 4) -> str:
    """Saca palabras de busqueda de la nota visual y el rotulo de la escena."""
    palabras: list[str] = []
    for texto in textos:
        for palabra in re.findall(r"[\wáéíóúñü]+", (texto or "").lower()):
            if len(palabra) > 2 and palabra not in PALABRAS_VACIAS and palabra not in palabras:
                palabras.append(palabra)
    return " ".join(palabras[:maximo])


def _cache(cfg: Config, origen: str, identificador: str) -> Path:
    nombre = re.sub(r"[^\w.-]", "_", f"{origen}-{identificador}")[:80]
    return cfg.cache_broll / f"{nombre}.mp4"


# --- proveedores -------------------------------------------------------------

def _pexels(cfg: Config, consulta: str, duracion: float) -> Clip | None:
    clave = os.environ.get("PEXELS_API_KEY", "").strip()
    if not clave:
        return None
    url = ("https://api.pexels.com/videos/search?"
           + urllib.parse.urlencode(
               {"query": consulta, "orientation": "portrait",
                "size": "medium", "per_page": 5}))
    datos = web.pedir(url, cabeceras={"Authorization": clave})
    for video in datos.get("videos") or []:
        # Preferimos el archivo vertical mas pequeno que aun sea >= 1080 de alto.
        archivos = sorted(
            (a for a in video.get("video_files") or [] if a.get("link")),
            key=lambda a: (a.get("height") or 0) < 1080,
        )
        if not archivos:
            continue
        destino = _cache(cfg, "pexels", str(video.get("id")))
        if not destino.is_file():
            web.descargar(archivos[0]["link"], destino)
        return Clip(destino, "pexels", (video.get("user") or {}).get("name", ""),
                    video.get("url", ""), consulta)
    return None


def _pixabay(cfg: Config, consulta: str, duracion: float) -> Clip | None:
    clave = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not clave:
        return None
    url = ("https://pixabay.com/api/videos/?"
           + urllib.parse.urlencode(
               {"key": clave, "q": consulta, "per_page": 5, "video_type": "film"}))
    datos = web.pedir(url)
    for video in datos.get("hits") or []:
        formatos = video.get("videos") or {}
        elegido = formatos.get("large") or formatos.get("medium") or formatos.get("small")
        if not (elegido or {}).get("url"):
            continue
        destino = _cache(cfg, "pixabay", str(video.get("id")))
        if not destino.is_file():
            web.descargar(elegido["url"], destino)
        return Clip(destino, "pixabay", video.get("user", ""),
                    video.get("pageURL", ""), consulta)
    return None


def _local(cfg: Config, consulta: str, duracion: float) -> Clip | None:
    """Busca en tu carpeta de clips: gana el archivo que comparte mas palabras."""
    if not cfg.broll_propio or not Path(cfg.broll_propio).is_dir():
        return None
    palabras = set(consulta.lower().split())
    mejor: tuple[int, Path] | None = None
    for ruta in sorted(Path(cfg.broll_propio).rglob("*")):
        if ruta.suffix.lower() not in (".mp4", ".mov", ".webm", ".mkv"):
            continue
        etiquetas = set(re.findall(r"[\wáéíóúñü]+", ruta.stem.lower()))
        coincidencias = len(palabras & etiquetas)
        if coincidencias and (mejor is None or coincidencias > mejor[0]):
            mejor = (coincidencias, ruta)
    if mejor is None:
        return None
    return Clip(mejor[1], "local", consulta=consulta)


def _generado(cfg: Config, consulta: str, duracion: float) -> Clip:
    """Degradado en movimiento. El color sale de la consulta, asi que cada
    escena tiene su tono y el video no parece una sola diapositiva."""
    semilla = int(hashlib.sha1(consulta.encode("utf-8")).hexdigest()[:6], 16)
    destino = _cache(cfg, "generado", f"{semilla}-{int(duracion * 10)}")
    if destino.is_file():
        return Clip(destino, "generado", consulta=consulta)

    ancho, _, alto = cfg.resolucion.partition("x")
    filtro = (f"gradients=s={ancho}x{alto}:d={max(duracion, 1):.2f}:speed=0.02:"
              f"n=3:seed={semilla},format=yuv420p")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", filtro, "-r", "30", "-t", f"{max(duracion, 1):.2f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", str(destino)],
        check=True, capture_output=True,
    )
    return Clip(destino, "generado", consulta=consulta)


PROVEEDORES = {"local": _local, "pexels": _pexels, "pixabay": _pixabay,
               "generado": _generado}
ORDEN_AUTO = ("local", "pexels", "pixabay", "generado")


def configurados() -> dict[str, bool]:
    return {"pexels": bool(os.environ.get("PEXELS_API_KEY")),
            "pixabay": bool(os.environ.get("PIXABAY_API_KEY"))}


def buscar(cfg: Config, consulta: str, duracion: float,
           avisos: list[str] | None = None) -> Clip:
    """Devuelve un clip para la escena. Nunca falla: acaba en 'generado'."""
    avisos = avisos if avisos is not None else []
    fuentes = (ORDEN_AUTO if cfg.broll == "auto" else (cfg.broll, "generado"))

    for nombre in fuentes:
        proveedor = PROVEEDORES.get(nombre)
        if proveedor is None:
            avisos.append(f"origen de b-roll desconocido: {nombre}")
            continue
        try:
            clip = proveedor(cfg, consulta, duracion)
        except (web.ErrorHttp, subprocess.CalledProcessError) as exc:
            avisos.append(f"b-roll {nombre} fallo para «{consulta}»: {exc}")
            continue
        if clip is not None:
            return clip
    return _generado(cfg, consulta, duracion)


def creditos(clips: list[Clip]) -> str:
    lineas = sorted({c.credito() for c in clips if c.credito()})
    if not lineas:
        return ""
    return ("B-roll usado en este video\n"
            "==========================\n\n" + "\n".join(lineas) + "\n")
