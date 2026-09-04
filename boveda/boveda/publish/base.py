"""Piezas comunes de los publicadores: HTTP, tipos y errores.

Se usa urllib de la libreria estandar a proposito: son cuatro llamadas HTTP por
red y no merece la pena arrastrar otra dependencia solo para esto.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TIEMPO_ESPERA = 180


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


@dataclass
class Resultado:
    id_remoto: str | None = None
    url_remota: str | None = None
    detalle: str = ""


def env(clave: str, red: str, obligatorio: bool = True) -> str:
    valor = os.environ.get(clave, "").strip()
    if not valor and obligatorio:
        raise FaltaConfiguracion(red, f"falta la variable {clave} en tu .env")
    return valor


def pedir(url: str, metodo: str = "GET", *, red: str = "http",
          cabeceras: dict[str, str] | None = None,
          json_datos: Any = None, form: dict[str, Any] | None = None,
          cuerpo: bytes | None = None, tipo: str | None = None,
          timeout: int = TIEMPO_ESPERA) -> Any:
    """Una peticion HTTP. Devuelve el JSON de la respuesta (o los bytes crudos)."""
    cabeceras = dict(cabeceras or {})
    datos: bytes | None = cuerpo

    if json_datos is not None:
        datos = json.dumps(json_datos).encode("utf-8")
        cabeceras.setdefault("Content-Type", "application/json")
    elif form is not None:
        datos = urllib.parse.urlencode(
            {k: v for k, v in form.items() if v is not None}
        ).encode("utf-8")
        cabeceras.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif tipo:
        cabeceras.setdefault("Content-Type", tipo)

    peticion = urllib.request.Request(url, data=datos, headers=cabeceras, method=metodo)
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            crudo = respuesta.read()
            cabecera_tipo = respuesta.headers.get("Content-Type", "")
            if "json" in cabecera_tipo and crudo:
                return json.loads(crudo)
            return {"_bytes": crudo, "_headers": dict(respuesta.headers),
                    "_status": respuesta.status}
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")[:800]
        raise ErrorRed(red, f"HTTP {exc.code}: {detalle}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise ErrorRed(red, f"no se pudo conectar: {exc.reason}") from exc


def subir_archivo(url: str, ruta: Path, *, red: str, metodo: str = "PUT",
                  cabeceras: dict[str, str] | None = None) -> Any:
    """Sube un archivo entero de una vez. Los videos de redes caben de sobra."""
    datos = ruta.read_bytes()
    tipo = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
    cabeceras = dict(cabeceras or {})
    cabeceras.setdefault("Content-Type", tipo)
    cabeceras.setdefault("Content-Length", str(len(datos)))
    return pedir(url, metodo, red=red, cabeceras=cabeceras, cuerpo=datos)


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
