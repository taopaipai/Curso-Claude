"""Piezas comunes de los publicadores: HTTP, tipos y errores.

El transporte HTTP vive en `boveda.web`, compartido con la busqueda de b-roll;
aqui solo se le pone encima el nombre de la red para que los errores digan quien
fallo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import web

TIEMPO_ESPERA = web.TIEMPO_ESPERA


class ErrorRed(RuntimeError):
    """Fallo al hablar con la API de una red. Lleva el cuerpo de la respuesta."""

    def __init__(self, red: str, mensaje: str, codigo: int | None = None):
        super().__init__(f"{red}: {mensaje}")
        self.red = red
        self.codigo = codigo


class FaltaConfiguracion(ErrorRed):
    """La red no tiene credenciales configuradas."""


@dataclass
class Publicacion:
    """Lo que se va a publicar, ya resuelto: texto, medio y destino."""

    produccion_id: int
    formato: str
    texto: str
    titulo: str | None = None
    media: Path | None = None
    media_url: str | None = None
    etiquetas: tuple[str, ...] = ()
    perfil: str | None = None          # nicho, para elegir las credenciales


@dataclass
class Resultado:
    id_remoto: str | None = None
    url_remota: str | None = None
    detalle: str = ""


def env(clave: str, red: str, perfil: str | None = None,
        obligatorio: bool = True) -> str:
    """Busca la credencial del nicho y, si no la hay, la general.

    Cada nicho es un perfil: IG_ACCESS_TOKEN__MARKETING antes que
    IG_ACCESS_TOKEN. Asi anadir una marca es anadir variables al .env, sin tocar
    codigo ni guardar un solo secreto en la base de datos.
    """
    candidatos = [f"{clave}__{perfil}"] if perfil else []
    candidatos.append(clave)
    for candidato in candidatos:
        valor = os.environ.get(candidato, "").strip()
        if valor:
            return valor
    if obligatorio:
        cual = " ni ".join(candidatos)
        raise FaltaConfiguracion(red, f"falta {cual} en tu .env")
    return ""


def _valor(clave: str, perfil: str | None) -> str:
    """La credencial del perfil si existe, si no la general. Sin exigirla."""
    return env(clave, "", perfil, obligatorio=False)


def pedir(url: str, metodo: str = "GET", *, red: str = "http", **kwargs: Any) -> Any:
    try:
        return web.pedir(url, metodo, **kwargs)
    except web.ErrorHttp as exc:
        raise ErrorRed(red, str(exc), exc.codigo) from exc


def subir_archivo(url: str, ruta: Path, *, red: str, metodo: str = "PUT",
                  cabeceras: dict[str, str] | None = None) -> Any:
    try:
        return web.subir_archivo(url, ruta, metodo=metodo, cabeceras=cabeceras)
    except web.ErrorHttp as exc:
        raise ErrorRed(red, str(exc), exc.codigo) from exc


def exigir_media(pub: Publicacion, red: str, clase: str = "video") -> Path:
    if pub.media is None or not Path(pub.media).is_file():
        raise ErrorRed(red, f"esta red necesita un archivo de {clase}: pasalo con --media")
    return Path(pub.media)


def exigir_url(pub: Publicacion, red: str) -> str:
    if not pub.media_url:
        raise ErrorRed(
            red,
            "esta red descarga el medio desde una URL publica: pasala con --media-url "
            "o define BOVEDA_MEDIA_BASE_URL",
        )
    return pub.media_url


def trocear_hilo(texto: str, limite: int = 280) -> list[str]:
    """Parte el cuerpo de un hilo en publicaciones.

    Respeta la division que ya trae el texto (parrafos o lineas numeradas) y solo
    corta por longitud cuando un bloque no cabe, buscando el final de frase.
    """
    bloques = [b.strip() for b in texto.split("\n\n") if b.strip()]
    if len(bloques) < 2:
        bloques = [l.strip() for l in texto.splitlines() if l.strip()]

    partes: list[str] = []
    for bloque in bloques:
        while len(bloque) > limite:
            corte = bloque.rfind(". ", 0, limite)
            if corte < limite // 2:
                corte = bloque.rfind(" ", 0, limite)
            if corte <= 0:
                corte = limite
            partes.append(bloque[:corte + 1].strip())
            bloque = bloque[corte + 1:].strip()
        if bloque:
            partes.append(bloque)
    return partes
