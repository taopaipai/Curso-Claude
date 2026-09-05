"""Publica hilos en X (API v2).

El cuerpo se parte en publicaciones respetando los parrafos del guion, y cada
una responde a la anterior para que salga como hilo y no como mensajes sueltos.
Solo texto: subir medios exige otra API y no hace falta para un hilo.
"""

from __future__ import annotations

import os

from ..config import Config
from .base import _valor, ErrorRed, Publicacion, Resultado, env, pedir, trocear_hilo
NOMBRE = "x"
NECESITA_MEDIA = None

BASE = "https://api.x.com/2"
LIMITE = 280


def configurada(cfg: Config, perfil: str | None = None) -> bool:
    return bool(_valor("X_ACCESS_TOKEN", perfil))


def _cabeceras(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def verificar(cfg: Config, perfil: str | None = None) -> str:
    token = env("X_ACCESS_TOKEN", NOMBRE, perfil)
    datos = pedir(f"{BASE}/users/me", red=NOMBRE, cabeceras=_cabeceras(token))
    usuario = (datos.get("data") or {})
    return f"@{usuario.get('username', '?')}"


def publicar(cfg: Config, pub: Publicacion) -> Resultado:
    perfil = pub.perfil
    token = env("X_ACCESS_TOKEN", NOMBRE, perfil)
    limite = int(os.environ.get("X_LIMITE_CARACTERES", LIMITE))
    partes = trocear_hilo(pub.texto, limite)
    if not partes:
        raise ErrorRed(NOMBRE, "no hay texto que publicar")

    primero: str | None = None
    anterior: str | None = None
    for parte in partes:
        cuerpo: dict[str, object] = {"text": parte}
        if anterior:
            cuerpo["reply"] = {"in_reply_to_tweet_id": anterior}
        datos = pedir(f"{BASE}/tweets", "POST", red=NOMBRE,
                      cabeceras=_cabeceras(token), json_datos=cuerpo)
        identificador = (datos.get("data") or {}).get("id")
        if not identificador:
            raise ErrorRed(NOMBRE, f"respuesta sin id de publicacion: {datos}")
        anterior = identificador
        primero = primero or identificador

    return Resultado(id_remoto=primero,
                     url_remota=f"https://x.com/i/web/status/{primero}",
                     detalle=f"hilo de {len(partes)} publicaciones")
