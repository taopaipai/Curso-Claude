"""HTTP minimo compartido: peticiones y descargas con la libreria estandar.

Lo usan la publicacion en redes y la busqueda de b-roll. Son un punado de
llamadas y no compensa arrastrar otra dependencia.
"""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TIEMPO_ESPERA = 180


class ErrorHttp(RuntimeError):
    def __init__(self, mensaje: str, codigo: int | None = None):
        super().__init__(mensaje)
        self.codigo = codigo


def pedir(url: str, metodo: str = "GET", *,
          cabeceras: dict[str, str] | None = None,
          json_datos: Any = None, form: dict[str, Any] | None = None,
          cuerpo: bytes | None = None, tipo: str | None = None,
          timeout: int = TIEMPO_ESPERA) -> Any:
    """Una peticion HTTP. Devuelve el JSON de la respuesta, o bytes y cabeceras."""
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
            if "json" in respuesta.headers.get("Content-Type", "") and crudo:
                return json.loads(crudo)
            return {"_bytes": crudo, "_headers": dict(respuesta.headers),
                    "_status": respuesta.status}
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")[:800]
        raise ErrorHttp(f"HTTP {exc.code}: {detalle}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise ErrorHttp(f"no se pudo conectar: {exc.reason}") from exc


def subir_archivo(url: str, ruta: Path, *, metodo: str = "PUT",
                  cabeceras: dict[str, str] | None = None) -> Any:
    """Sube un archivo entero de una vez. Los videos de redes caben de sobra."""
    datos = ruta.read_bytes()
    cabeceras = dict(cabeceras or {})
    cabeceras.setdefault("Content-Type",
                         mimetypes.guess_type(ruta.name)[0] or "application/octet-stream")
    cabeceras.setdefault("Content-Length", str(len(datos)))
    return pedir(url, metodo, cabeceras=cabeceras, cuerpo=datos)


def descargar(url: str, destino: Path, *, cabeceras: dict[str, str] | None = None,
              timeout: int = TIEMPO_ESPERA) -> Path:
    """Baja un archivo a disco. Escribe primero en .parcial para no dejar
    a medias un archivo que luego se daria por bueno desde la cache."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".parcial")
    peticion = urllib.request.Request(url, headers=dict(cabeceras or {}))
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            parcial.write_bytes(respuesta.read())
    except urllib.error.HTTPError as exc:
        raise ErrorHttp(f"HTTP {exc.code} al descargar {url}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise ErrorHttp(f"no se pudo descargar {url}: {exc.reason}") from exc
    parcial.replace(destino)
    return destino
