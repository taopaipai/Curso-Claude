"""Destino local: deja la publicacion lista en disco en vez de subirla.

Es el destino por defecto y el que usa el modo ensayo. Sirve tal cual para el
flujo manual: te deja el texto y el medio en una carpeta por red y por fecha,
listos para copiar y pegar.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from .base import Publicacion, Resultado

NOMBRE = "archivo"
NECESITA_MEDIA = None


def configurada(cfg: Config) -> bool:
    return True


def verificar(cfg: Config) -> str:
    return f"carpeta local {cfg.publicaciones}"


def publicar(cfg: Config, pub: Publicacion) -> Resultado:
    sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destino = cfg.publicaciones / f"{sello}-{pub.produccion_id:05d}-{pub.formato}"
    destino.mkdir(parents=True, exist_ok=True)

    texto = destino / "texto.md"
    encabezado = f"# {pub.titulo or pub.formato}\n\n" if pub.titulo else ""
    etiquetas = ("\n\n" + " ".join(f"#{e}" for e in pub.etiquetas)) if pub.etiquetas else ""
    texto.write_text(encabezado + pub.texto + etiquetas, encoding="utf-8")

    if pub.media and Path(pub.media).is_file():
        shutil.copy2(pub.media, destino / Path(pub.media).name)

    return Resultado(id_remoto=destino.name, url_remota=destino.as_uri(),
                     detalle=f"guardado en {destino}")
