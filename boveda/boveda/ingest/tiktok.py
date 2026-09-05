"""Importa favoritos y me-gusta de TikTok desde `user_data.json`.

TikTok ha movido las claves entre versiones del export, asi que se busca
por nombre en cualquier nivel en vez de asumir una ruta fija.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from .base import canonizar, extraer_urls, normalizar_fecha

# nombre de la lista en el export -> carpeta que le asignamos
LISTAS = {
    "FavoriteVideoList": "favoritos",
    "ItemFavoriteList": "favoritos",
    "ItemHistoryList": "historial",
    "Like List": "me-gusta",
    "ItemLikeList": "me-gusta",
}


def _recorrer(nodo: Any, ruta: str = "") -> Iterator[tuple[str, list[Any]]]:
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            if isinstance(valor, list) and clave in LISTAS:
                yield clave, valor
            else:
                yield from _recorrer(valor, f"{ruta}/{clave}")


def importar(ruta: Path, carpeta: str | None = None) -> list[dict[str, Any]]:
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    items: list[dict[str, Any]] = []
    vistos: set[str] = set()

    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        datos = None

    if datos is not None:
        for clave, lista in _recorrer(datos):
            for registro in lista:
                if not isinstance(registro, dict):
                    continue
                url = registro.get("Link") or registro.get("link") or registro.get("VideoLink")
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
                    "plataforma": plataforma if plataforma != "web" else "tiktok",
                    "url_canonica": canonica,
                    "id_externo": externo,
                    "autor": autor,
                    "guardado_en": normalizar_fecha(registro.get("Date") or registro.get("date")),
                    "carpeta": carpeta or LISTAS[clave],
                    "crudo_json": json.dumps(registro, ensure_ascii=False),
                })

    if not items:  # export en txt/html: una URL por linea
        for url in extraer_urls(texto):
            if "tiktok.com" not in url:
                continue
            try:
                canonica, _, externo, autor = canonizar(url)
            except ValueError:
                continue
            if canonica in vistos:
                continue
            vistos.add(canonica)
            items.append({
                "plataforma": "tiktok",
                "url_canonica": canonica,
                "id_externo": externo,
                "autor": autor,
                "carpeta": carpeta,
            })
    return items
