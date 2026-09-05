"""Importa playlists de YouTube: CSV de Google Takeout o una URL de playlist.

Takeout deja un CSV por playlist en `Takeout/YouTube y YouTube Music/playlists/`
con dos bloques separados por una linea en blanco; el segundo es el que trae
`Video ID` y la fecha en que se anadio.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path
from typing import Any

from .base import canonizar, extraer_urls, normalizar_fecha


def _desde_csv(texto: str, carpeta: str | None, nombre: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    # Nos quedamos con el bloque que tenga cabecera de videos.
    for bloque in texto.split("\n\n"):
        bloque = bloque.strip()
        if not bloque:
            continue
        lector = csv.DictReader(io.StringIO(bloque))
        campos = [c.strip().lower() for c in (lector.fieldnames or [])]
        if not any("video id" in c for c in campos):
            continue
        col_id = (lector.fieldnames or [])[
            next(i for i, c in enumerate(campos) if "video id" in c)
        ]
        col_fecha = next(
            (f for f in (lector.fieldnames or []) if "timestamp" in f.strip().lower()), None
        )
        for fila in lector:
            vid = (fila.get(col_id) or "").strip()
            if len(vid) != 11:
                continue
            items.append({
                "plataforma": "youtube",
                "url_canonica": f"https://www.youtube.com/watch?v={vid}",
                "id_externo": vid,
                "guardado_en": normalizar_fecha(fila.get(col_fecha)) if col_fecha else None,
                "carpeta": carpeta or nombre,
            })
    return items


def _desde_playlist(url: str, carpeta: str | None) -> list[dict[str, Any]]:
    """Expande una playlist/canal con yt-dlp sin descargar nada."""
    salida = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-single-json", url],
        capture_output=True, text=True, check=True,
    )
    datos = json.loads(salida.stdout)
    nombre = datos.get("title") or "playlist"
    items: list[dict[str, Any]] = []
    for entrada in datos.get("entries") or []:
        vid = entrada.get("id")
        if not vid:
            continue
        items.append({
            "plataforma": "youtube",
            "url_canonica": f"https://www.youtube.com/watch?v={vid}",
            "id_externo": vid,
            "autor": entrada.get("uploader") or entrada.get("channel"),
            "titulo": entrada.get("title"),
            "duracion_seg": int(entrada["duration"]) if entrada.get("duration") else None,
            "carpeta": carpeta or nombre,
            "crudo_json": json.dumps(entrada, ensure_ascii=False),
        })
    return items


def importar(origen: Path | str, carpeta: str | None = None) -> list[dict[str, Any]]:
    if isinstance(origen, str) and origen.startswith("http"):
        return _desde_playlist(origen, carpeta)

    ruta = Path(origen)
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    if ruta.suffix.lower() == ".csv":
        items = _desde_csv(texto, carpeta, ruta.stem)
        if items:
            return items

    items, vistos = [], set()
    for url in extraer_urls(texto):
        try:
            canonica, plataforma, externo, _ = canonizar(url)
        except ValueError:
            continue
        if plataforma != "youtube" or canonica in vistos:
            continue
        vistos.add(canonica)
        items.append({
            "plataforma": "youtube",
            "url_canonica": canonica,
            "id_externo": externo,
            "carpeta": carpeta or ruta.stem,
        })
    return items
