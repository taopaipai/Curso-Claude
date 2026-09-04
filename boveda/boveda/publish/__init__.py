"""Publicadores por red. Cada modulo expone configurada(), verificar() y publicar()."""

from . import archivo, instagram, tiktok, x, youtube

REDES = {
    m.NOMBRE: m for m in (archivo, instagram, tiktok, youtube, x)
}

# Que formato de produccion tiene sentido en cada red. Sirve para avisar, no
# para prohibir: si insistes con --forzar, se publica igual.
FORMATOS = {
    "instagram": {"reel", "carrusel", "short"},
    "tiktok": {"reel", "short"},
    "youtube": {"reel", "short", "blog"},
    "x": {"hilo"},
    "archivo": {"reel", "short", "carrusel", "hilo", "newsletter", "blog"},
}

__all__ = ["REDES", "FORMATOS"]
