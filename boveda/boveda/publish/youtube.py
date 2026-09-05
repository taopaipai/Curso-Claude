"""Sube shorts y videos a YouTube (Data API v3, subida reanudable).

Autenticacion por OAuth de aplicacion instalada: guardas un refresh token una
vez y aqui se canjea por un access token en cada ejecucion.

Un video de menos de 60 s en vertical entra solo como Short; no hay parametro
para pedirlo.
"""

from __future__ import annotations

import json
import os

from ..config import Config
from .base import _valor, ErrorRed, Publicacion, Resultado, env, exigir_media, pedir, subir_archivo
NOMBRE = "youtube"
NECESITA_MEDIA = "video"

SUBIDA = ("https://www.googleapis.com/upload/youtube/v3/videos"
          "?uploadType=resumable&part=snippet,status")
TOKEN = "https://oauth2.googleapis.com/token"


def configurada(cfg: Config, perfil: str | None = None) -> bool:
    return all(_valor(c, perfil) for c in
               ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def _token(cfg: Config, perfil: str | None = None) -> str:
    datos = pedir(TOKEN, "POST", red=NOMBRE, form={
        "client_id": env("YT_CLIENT_ID", NOMBRE, perfil),
        "client_secret": env("YT_CLIENT_SECRET", NOMBRE, perfil),
        "refresh_token": env("YT_REFRESH_TOKEN", NOMBRE, perfil),
        "grant_type": "refresh_token",
    })
    acceso = datos.get("access_token")
    if not acceso:
        raise ErrorRed(NOMBRE, f"no se pudo renovar el token: {datos}")
    return acceso


def verificar(cfg: Config, perfil: str | None = None) -> str:
    acceso = _token(cfg, perfil)
    datos = pedir("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                  red=NOMBRE, cabeceras={"Authorization": f"Bearer {acceso}"})
    items = datos.get("items") or []
    if not items:
        raise ErrorRed(NOMBRE, "el token no tiene ningun canal asociado")
    return items[0]["snippet"]["title"]


def publicar(cfg: Config, pub: Publicacion) -> Resultado:
    perfil = pub.perfil
    video = exigir_media(pub, NOMBRE)
    acceso = _token(cfg, perfil)
    privacidad = os.environ.get("YT_PRIVACY", "private")

    lineas = pub.texto.strip().splitlines()
    titulo = (pub.titulo or (lineas[0] if lineas else "Sin titulo")).lstrip("# ").strip()
    metadatos = {
        "snippet": {
            "title": titulo[:100],
            "description": pub.texto[:5000],
            "tags": list(pub.etiquetas)[:15],
            "categoryId": os.environ.get("YT_CATEGORY_ID", "22"),
        },
        "status": {"privacyStatus": privacidad, "selfDeclaredMadeForKids": False},
    }

    inicio = pedir(SUBIDA, "POST", red=NOMBRE, cabeceras={
        "Authorization": f"Bearer {acceso}",
        "X-Upload-Content-Type": "video/*",
        "X-Upload-Content-Length": str(video.stat().st_size),
    }, json_datos=metadatos)

    destino = (inicio.get("_headers") or {}).get("Location") if isinstance(inicio, dict) else None
    if not destino:
        raise ErrorRed(NOMBRE, f"YouTube no devolvio la URL de subida: {inicio}")

    respuesta = subir_archivo(destino, video, red=NOMBRE,
                              cabeceras={"Authorization": f"Bearer {acceso}"})
    if isinstance(respuesta, dict) and "_bytes" in respuesta:
        respuesta = json.loads(respuesta["_bytes"] or b"{}")
    id_remoto = respuesta.get("id")
    return Resultado(
        id_remoto=id_remoto,
        url_remota=f"https://www.youtube.com/watch?v={id_remoto}" if id_remoto else None,
        detalle=f"subido a YouTube como '{privacidad}'",
    )
