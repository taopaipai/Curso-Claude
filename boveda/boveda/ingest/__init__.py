"""Importadores: convierten un export de plataforma en filas de `items`."""

from .instagram import importar as importar_instagram
from .tiktok import importar as importar_tiktok
from .youtube import importar as importar_youtube
from .urls import importar as importar_urls

IMPORTADORES = {
    "instagram": importar_instagram,
    "tiktok": importar_tiktok,
    "youtube": importar_youtube,
    "urls": importar_urls,
}

__all__ = ["IMPORTADORES", "importar_instagram", "importar_tiktok",
           "importar_youtube", "importar_urls"]
