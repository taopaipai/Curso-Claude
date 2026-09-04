"""Publica videos en TikTok (Content Posting API).

Dos caminos segun lo que tengas:
  FILE_UPLOAD    -> se sube el archivo local. Es el camino por defecto.
  PULL_FROM_URL  -> TikTok descarga el video de una URL tuya. Solo funciona si
                    el dominio esta verificado en el portal de desarrolladores.

Mientras la app no pase la auditoria de TikTok, la unica privacidad permitida es
SELF_ONLY (el video queda privado en tu cuenta). Por eso el valor por defecto es
ese: publicar en publico sin auditoria falla, y es mejor que falle aqui.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..config import Config
from .base import (ErrorRed, Publicacion, Resultado, env, exigir_media, pedir,
                   subir_archivo)

NOMBRE = "tiktok"
NECESITA_MEDIA = "video"

BASE = "https://open.tiktokapis.com/v2"
INTENTOS_ESTADO = 20
ESPERA_ESTADO = 15


def configurada(cfg: Config) -> bool:
    return bool(os.environ.get("TIKTOK_ACCESS_TOKEN"))


def _cabeceras(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


def verificar(cfg: Config) -> str:
    token = env("TIKTOK_ACCESS_TOKEN", NOMBRE)
    datos = pedir(f"{BASE}/post/publish/creator_info/query/", "POST", red=NOMBRE,
                  cabeceras=_cabeceras(token), json_datos={})
    info = (datos.get("data") or {})
    permisos = ", ".join(info.get("privacy_level_options") or [])
    return f"@{info.get('creator_username', '?')} (privacidad permitida: {permisos or '?'})"


def _esperar_publicacion(publish_id: str, token: str) -> dict:
    for _ in range(INTENTOS_ESTADO):
        datos = pedir(f"{BASE}/post/publish/status/fetch/", "POST", red=NOMBRE,
                      cabeceras=_cabeceras(token), json_datos={"publish_id": publish_id})
        info = datos.get("data") or {}
        estado = info.get("status")
        if estado in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            return info
        if estado == "FAILED":
            raise ErrorRed(NOMBRE, f"TikTok rechazo el video: {info.get('fail_reason')}")
        time.sleep(ESPERA_ESTADO)
    raise ErrorRed(NOMBRE, "TikTok sigue procesando el video despues de varios minutos")


def publicar(cfg: Config, pub: Publicacion) -> Resultado:
    token = env("TIKTOK_ACCESS_TOKEN", NOMBRE)
    privacidad = os.environ.get("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")

    info_post = {
        "title": (pub.titulo or pub.texto)[:2200],
        "privacy_level": privacidad,
        "disable_duet": False,
        "disable_comment": False,
        "disable_stitch": False,
    }

    if pub.media_url:
        cuerpo = {"post_info": info_post,
                  "source_info": {"source": "PULL_FROM_URL", "video_url": pub.media_url}}
        video = None
    else:
        video = exigir_media(pub, NOMBRE)
        tamano = video.stat().st_size
        cuerpo = {"post_info": info_post, "source_info": {
            "source": "FILE_UPLOAD", "video_size": tamano,
            "chunk_size": tamano, "total_chunk_count": 1}}

    inicio = pedir(f"{BASE}/post/publish/video/init/", "POST", red=NOMBRE,
                   cabeceras=_cabeceras(token), json_datos=cuerpo)
    datos = inicio.get("data") or {}
    publish_id = datos.get("publish_id")
    if not publish_id:
        raise ErrorRed(NOMBRE, f"respuesta sin publish_id: {inicio}")

    if video is not None:
        tamano = video.stat().st_size
        subir_archivo(
            datos["upload_url"], video, red=NOMBRE,
            cabeceras={"Content-Range": f"bytes 0-{tamano - 1}/{tamano}",
                       "Content-Type": "video/mp4"},
        )

    info = _esperar_publicacion(publish_id, token)
    enlaces = info.get("publicaly_available_post_id") or info.get("public_post_id") or []
    id_remoto = enlaces[0] if isinstance(enlaces, list) and enlaces else publish_id
    aviso = " (privado: la app aun no esta auditada)" if privacidad == "SELF_ONLY" else ""
    return Resultado(id_remoto=str(id_remoto), detalle=f"publicado en TikTok{aviso}")
