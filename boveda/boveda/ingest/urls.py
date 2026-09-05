"""Importa una lista suelta de enlaces (lo que copias a mano o compartes al vuelo).

Formato del archivo:
    # nombre-de-carpeta        <- cambia la carpeta de los enlaces siguientes
    https://...                <- un enlace por linea
    https://...  , copywriting <- o enlace + carpeta separados por coma o tab
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import canonizar

SEPARADOR = re.compile(r"[\t,;|]")


def importar(ruta: Path, carpeta: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    vistos: set[str] = set()
    actual = carpeta

    for linea in Path(ruta).read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith("#"):
            actual = linea.lstrip("#").strip() or carpeta
            continue

        partes = [p.strip() for p in SEPARADOR.split(linea) if p.strip()]
        url = partes[0]
        etiqueta = partes[1] if len(partes) > 1 else None
        try:
            canonica, plataforma, externo, autor = canonizar(url)
        except ValueError:
            continue
        if canonica in vistos:
            continue
        vistos.add(canonica)
        items.append({
            "plataforma": plataforma,
            "url_canonica": canonica,
            "id_externo": externo,
            "autor": autor,
            "carpeta": etiqueta or actual,
        })
    return items
