"""Orquesta la publicacion: aprobar, programar, ejecutar la cola y registrar.

Tres reglas que no se saltan:
  1. Solo se publica lo aprobado a mano. Un borrador nunca sale.
  2. Cada publicacion queda registrada antes y despues del intento, con el id
     que devuelve la red.
  3. La misma produccion no se publica dos veces en la misma red (indice unico
     en la base, no solo una comprobacion aqui).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import montaje
from .config import Config
from .publish import FORMATOS, REDES
from .publish.base import ErrorRed, Publicacion, Resultado


class ErrorPublicacion(RuntimeError):
    pass


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def aprobar(con: sqlite3.Connection, produccion_id: int, estado: str = "aprobado") -> None:
    cur = con.execute("UPDATE producciones SET estado = ? WHERE id = ?",
                      (estado, produccion_id))
    if not cur.rowcount:
        raise ErrorPublicacion(f"no existe la produccion {produccion_id}")
    con.commit()


def _produccion(con: sqlite3.Connection, produccion_id: int) -> sqlite3.Row:
    fila = con.execute(
        """
        SELECT p.*, a.analisis_json, i.url_canonica
        FROM producciones p
        JOIN items i          ON i.id = p.item_id
        LEFT JOIN analisis a  ON a.item_id = p.item_id
        WHERE p.id = ?
        """,
        (produccion_id,),
    ).fetchone()
    if fila is None:
        raise ErrorPublicacion(f"no existe la produccion {produccion_id}")
    return fila


def programar(con: sqlite3.Connection, produccion_id: int, red: str, *,
              cuando: str | None = None, media: str | None = None,
              media_url: str | None = None, forzar: bool = False) -> int:
    """Deja una publicacion en la cola. No publica nada todavia."""
    if red not in REDES:
        raise ErrorPublicacion(f"red desconocida: {red}. Opciones: {', '.join(sorted(REDES))}")

    fila = _produccion(con, produccion_id)

    # Primero lo irreversible: si ya salio en esta red, no se repite ni con --forzar.
    ya = con.execute(
        "SELECT id FROM publicaciones WHERE produccion_id = ? AND red = ? AND estado = 'publicada'",
        (produccion_id, red),
    ).fetchone()
    if ya:
        raise ErrorPublicacion(
            f"la produccion {produccion_id} ya se publico en {red} (publicacion #{ya['id']})"
        )

    # 'publicado' vale: la misma pieza puede salir en varias redes.
    if fila["estado"] not in ("aprobado", "publicado") and not forzar:
        raise ErrorPublicacion(
            f"la produccion {produccion_id} esta en '{fila['estado']}': "
            f"apruebala con 'boveda aprobar {produccion_id}' antes de publicar"
        )
    if fila["formato"] not in FORMATOS.get(red, set()) and not forzar:
        raise ErrorPublicacion(
            f"el formato '{fila['formato']}' no encaja en {red} "
            f"(esperado: {', '.join(sorted(FORMATOS.get(red, set())))}). Usa --forzar si insistes."
        )

    if media and not Path(media).is_file():
        raise ErrorPublicacion(f"no existe el archivo de medio: {media}")

    cur = con.execute(
        "INSERT INTO publicaciones (produccion_id, red, estado, programado_para, "
        "media_ruta, media_url) VALUES (?, ?, 'programada', ?, ?, ?)",
        (produccion_id, red, cuando, media, media_url),
    )
    con.commit()
    return int(cur.lastrowid)


def pendientes(con: sqlite3.Connection, limite: int | None = None,
               red: str | None = None, incluir_futuras: bool = False) -> list[sqlite3.Row]:
    sql = ("SELECT * FROM publicaciones WHERE estado = 'programada'")
    args: list[Any] = []
    if not incluir_futuras:
        sql += " AND (programado_para IS NULL OR programado_para <= ?)"
        args.append(ahora())
    if red:
        sql += " AND red = ?"
        args.append(red)
    sql += " ORDER BY COALESCE(programado_para, creado_en), id"
    if limite:
        sql += f" LIMIT {int(limite)}"
    return con.execute(sql, args).fetchall()


def _armar(cfg: Config, con: sqlite3.Connection, fila: sqlite3.Row) -> Publicacion:
    prod = _produccion(con, fila["produccion_id"])
    analisis = json.loads(prod["analisis_json"]) if prod["analisis_json"] else {}

    media = Path(fila["media_ruta"]) if fila["media_ruta"] else None
    # Si no se paso un medio a mano, se usa el video montado de esta produccion.
    if media is None:
        media = montaje.video_de(con, prod["id"])
    url = fila["media_url"]
    # Si sirves los videos montados desde una carpeta publica, la URL se deduce.
    if not url and media and cfg.url_base_media:
        url = f"{cfg.url_base_media}/{media.name}"

    return Publicacion(
        produccion_id=prod["id"],
        formato=prod["formato"],
        texto=prod["cuerpo"],
        titulo=prod["titulo"],
        media=media,
        media_url=url,
        etiquetas=tuple(analisis.get("etiquetas") or ()),
    )


def ejecutar(cfg: Config, con: sqlite3.Connection, fila: sqlite3.Row,
             ensayo: bool = True) -> Resultado:
    """Publica una fila de la cola. En modo ensayo no toca la red ni la fila."""
    modulo = REDES[fila["red"]]
    pub = _armar(cfg, con, fila)

    if ensayo:
        aviso = []
        if not modulo.configurada(cfg):
            aviso.append("SIN CREDENCIALES")
        if getattr(modulo, "NECESITA_MEDIA", None) and not (pub.media or pub.media_url):
            aviso.append("FALTA MEDIO")
        return Resultado(detalle="ensayo: no se publico nada"
                                 + (f" [{', '.join(aviso)}]" if aviso else ""))

    con.execute("UPDATE publicaciones SET intentos = intentos + 1 WHERE id = ?", (fila["id"],))
    con.commit()
    try:
        resultado = modulo.publicar(cfg, pub)
    except ErrorRed as exc:
        con.execute("UPDATE publicaciones SET estado = 'error', error = ? WHERE id = ?",
                    (str(exc), fila["id"]))
        con.commit()
        raise
    except Exception as exc:  # noqa: BLE001
        con.execute("UPDATE publicaciones SET estado = 'error', error = ? WHERE id = ?",
                    (f"{type(exc).__name__}: {exc}", fila["id"]))
        con.commit()
        raise

    con.execute(
        "UPDATE publicaciones SET estado = 'publicada', publicado_en = ?, "
        "id_remoto = ?, url_remota = ?, error = NULL WHERE id = ?",
        (ahora(), resultado.id_remoto, resultado.url_remota, fila["id"]),
    )
    con.execute("UPDATE producciones SET estado = 'publicado' WHERE id = ?",
                (fila["produccion_id"],))
    con.commit()
    return resultado


def cancelar(con: sqlite3.Connection, publicacion_id: int) -> None:
    cur = con.execute(
        "UPDATE publicaciones SET estado = 'cancelada' WHERE id = ? AND estado <> 'publicada'",
        (publicacion_id,),
    )
    if not cur.rowcount:
        raise ErrorPublicacion(
            f"la publicacion {publicacion_id} no existe o ya esta publicada (no se puede deshacer)"
        )
    con.commit()
