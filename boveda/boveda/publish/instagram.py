"""Publica reels y carruseles en Instagram (Content Publishing API).

Flujo oficial en tres pasos: se crea un contenedor, se espera a que Instagram
termine de procesar el medio y se publica el contenedor.

Requisitos que impone Meta, no este codigo:
  - cuenta profesional (empresa o creador) vinculada a una pagina de Facebook
  - token de larga duracion con instagram_business_content_publish
  - el medio se descarga desde una URL PUBLICA: Instagram no acepta subidas
    directas por esta API. De ahi BOVEDA_MEDIA_BASE_URL.
"""

from __future__ import annotations

import os
import time

from ..config import Config
from .base import ErrorRed, Publicacion, Resultado, env, exigir_url, pedir

NOMBRE = "instagram"
NECESITA_MEDIA = "video"

INTENTOS_PROCESADO = 20
ESPERA_PROCESADO = 15


def _base() -> str:
    version = os.environ.get("BOVEDA_IG_API_VERSION", "v21.0")
    return f"https://graph.facebook.com/{version}"


def configurada(cfg: Config) -> bool:
    return bool(os.environ.get("IG_USER_ID") and os.environ.get("IG_ACCESS_TOKEN"))


def verificar(cfg: Config) -> str:
    usuario = env("IG_USER_ID", NOMBRE)
    token = env("IG_ACCESS_TOKEN", NOMBRE)
    datos = pedir(f"{_base()}/{usuario}?fields=username,name&access_token={token}",
                  red=NOMBRE)
    return f"@{datos.get('username', '?')} ({datos.get('name', '')})".strip()


def _esperar_contenedor(contenedor: str, token: str) -> None:
    """Publicar antes de que el contenedor este listo devuelve un 400."""
    for _ in range(INTENTOS_PROCESADO):
        estado = pedir(
            f"{_base()}/{contenedor}?fields=status_code,status&access_token={token}",
            red=NOMBRE,
        )
        codigo = estado.get("status_code")
        if codigo == "FINISHED":
            return
        if codigo == "ERROR":
            raise ErrorRed(NOMBRE, f"Instagram no pudo procesar el medio: {estado.get('status')}")
        time.sleep(ESPERA_PROCESADO)
    raise ErrorRed(NOMBRE, "el medio sigue procesandose despues de varios minutos")


def publicar(cfg: Config, pub: Publicacion) -> Resultado:
    usuario = env("IG_USER_ID", NOMBRE)
    token = env("IG_ACCESS_TOKEN", NOMBRE)
    url_media = exigir_url(pub, NOMBRE)
    pie = pub.texto[:2200]

    campos = {"caption": pie, "access_token": token}
    if url_media.lower().endswith((".jpg", ".jpeg", ".png")):
        campos["image_url"] = url_media
    else:
        campos["media_type"] = "REELS"
        campos["video_url"] = url_media

    contenedor = pedir(f"{_base()}/{usuario}/media", "POST", red=NOMBRE, form=campos)
    id_contenedor = contenedor.get("id")
    if not id_contenedor:
        raise ErrorRed(NOMBRE, f"respuesta sin id de contenedor: {contenedor}")

    if "video_url" in campos:
        _esperar_contenedor(id_contenedor, token)

    publicado = pedir(
        f"{_base()}/{usuario}/media_publish", "POST", red=NOMBRE,
        form={"creation_id": id_contenedor, "access_token": token},
    )
    id_remoto = publicado.get("id")
    permalink = ""
    try:
        detalle = pedir(f"{_base()}/{id_remoto}?fields=permalink&access_token={token}",
                        red=NOMBRE)
        permalink = detalle.get("permalink", "")
    except ErrorRed:
        pass  # el permalink es un extra; ya esta publicado
    return Resultado(id_remoto=id_remoto, url_remota=permalink,
                     detalle="publicado en Instagram")
