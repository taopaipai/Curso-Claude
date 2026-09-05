"""Utilidades compartidas por los importadores."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

RE_IG = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
RE_TT = re.compile(r"tiktok\.com/@([^/]+)/video/(\d+)")
RE_YT = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def canonizar(url: str) -> tuple[str, str, str | None, str | None]:
    """Normaliza una URL a (url_canonica, plataforma, id_externo, autor).

    Quitar parametros de tracking es lo que hace que la deduplicacion funcione:
    el mismo reel compartido dos veces llega con `?igshid=` distintos.
    """
    url = url.strip()
    if not url:
        raise ValueError("URL vacia")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if m := RE_IG.search(url):
        return f"https://www.instagram.com/p/{m.group(1)}/", "instagram", m.group(1), None
    if m := RE_TT.search(url):
        autor, vid = m.group(1), m.group(2)
        return f"https://www.tiktok.com/@{autor}/video/{vid}", "tiktok", vid, autor
    if m := RE_YT.search(url):
        return f"https://www.youtube.com/watch?v={m.group(1)}", "youtube", m.group(1), None

    partes = urlparse(url)
    if partes.netloc.endswith("tiktok.com") and "/t/" in partes.path:
        # Enlace corto de TikTok: se resuelve al descargar; conservamos el original.
        return url.split("?")[0], "tiktok", None, None
    if "v" in parse_qs(partes.query):
        vid = parse_qs(partes.query)["v"][0]
        return f"https://www.youtube.com/watch?v={vid}", "youtube", vid, None
    return url.split("?")[0], "web", None, None


def desde_epoch(valor: object) -> str | None:
    """Convierte un timestamp Unix (segundos) a ISO-8601 UTC."""
    try:
        segundos = int(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if segundos <= 0:
        return None
    return datetime.fromtimestamp(segundos, tz=timezone.utc).isoformat()


def normalizar_fecha(texto: object) -> str | None:
    """Acepta los formatos de fecha que aparecen en los exports."""
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return desde_epoch(texto)
    texto = str(texto).strip()
    if not texto:
        return None
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%d", "%b %d, %Y, %I:%M:%S %p"):
        try:
            return datetime.strptime(texto, formato).isoformat()
        except ValueError:
            continue
    return texto


def extraer_urls(texto: str) -> list[str]:
    """Rescata URLs de cualquier archivo (HTML exportado, notas, csv raro)."""
    return re.findall(r"https?://[^\s\"'<>)\]]+", texto)
