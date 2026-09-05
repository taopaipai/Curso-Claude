"""Importa los guardados de Instagram desde el export oficial de datos.

Ruta tipica dentro del ZIP de "Descargar tu informacion":
    your_instagram_activity/saved/saved_posts.json
    your_instagram_activity/saved/saved_collections.json

Tambien acepta el export en HTML o cualquier archivo suelto con enlaces:
en ese caso se extraen las URLs y se pierde solo la fecha de guardado.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from .base import canonizar, desde_epoch, extraer_urls

CLAVES_LISTA = ("saved_saved_media", "saved_saved_collections", "saved_media")


def _registros(datos: Any) -> Iterator[dict[str, Any]]:
    if isinstance(datos, list):
        yield from (d for d in datos if isinstance(d, dict))
        return
    if not isinstance(datos, dict):
        return
    for clave in CLAVES_LISTA:
        for registro in datos.get(clave, []) or []:
            if isinstance(registro, dict):
                yield registro


def _campos(registro: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    """Devuelve (url, timestamp, coleccion) de un registro `string_map_data`."""
    mapa = registro.get("string_map_data") or {}
    for clave, valor in mapa.items():
        if not isinstance(valor, dict):
            continue
        href = valor.get("href")
        if href:
            return href, valor.get("timestamp"), clave if clave != "Saved on" else None
    return None, None, None


def importar(ruta: Path, carpeta: str | None = None) -> list[dict[str, Any]]:
    """Lee el archivo y devuelve items listos para insertar."""
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    items: list[dict[str, Any]] = []
    vistos: set[str] = set()

    datos: Any = None
    if ruta.suffix.lower() == ".json":
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError:
            datos = None

    if datos is not None:
        for registro in _registros(datos):
            # En saved_collections.json cada coleccion trae su propia lista.
            anidados = registro.get("media_list_data") or registro.get("media") or []
            candidatos = [registro] + [d for d in anidados if isinstance(d, dict)]
            nombre_coleccion = registro.get("title") if anidados else None
            for candidato in candidatos:
                url, ts, etiqueta = _campos(candidato)
                if not url:
                    continue
                try:
                    canonica, plataforma, externo, autor = canonizar(url)
                except ValueError:
                    continue
                if canonica in vistos:
                    continue
                vistos.add(canonica)
                items.append({
                    "plataforma": plataforma if plataforma != "web" else "instagram",
                    "url_canonica": canonica,
                    "id_externo": externo,
                    "autor": autor or (registro.get("title") if not anidados else None),
                    "guardado_en": desde_epoch(ts),
                    "carpeta": carpeta or nombre_coleccion or etiqueta,
                    "crudo_json": json.dumps(candidato, ensure_ascii=False),
                })

    if not items:  # HTML u otro formato: rescatamos los enlaces
        for url in extraer_urls(texto):
            if "instagram.com" not in url:
                continue
            try:
                canonica, _, externo, autor = canonizar(url)
            except ValueError:
                continue
            if canonica in vistos or "/p/" not in canonica:
                continue
            vistos.add(canonica)
            items.append({
                "plataforma": "instagram",
                "url_canonica": canonica,
                "id_externo": externo,
                "autor": autor,
                "carpeta": carpeta,
            })
    return items
