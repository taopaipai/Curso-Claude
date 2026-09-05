"""Captura de los comentarios mas votados y de las metricas de cada publicacion.

Lo que se puede sacar cambia mucho segun la plataforma, y conviene saberlo antes
de esperar datos que no van a llegar:

                       YouTube        TikTok        Instagram
  vistas / likes       si             si            a veces
  nº de comentarios    si             si            si
  compartidos          no             si            no
  GUARDADOS            no             SI            no
  top comentarios      si (por votos) NO            parcial
  fecha del comentario aproximada     -             exacta

TikTok es la unica que publica el numero de guardados. Y es la unica de la que
no se pueden extraer comentarios: yt-dlp no los implementa para TikTok.

En YouTube la fecha del comentario se deduce de un texto tipo "hace 3 meses",
asi que es una estimacion; se marca como tal en `tiempo_exacto` para que nadie
la trate como un dato duro.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

TOPE = 20

CAPACIDADES: dict[str, dict[str, Any]] = {
    "youtube": {"comentarios": True, "tiempo_exacto": False, "guardados": False,
                "nota": "los comentarios se piden ordenados por votos; la fecha es aproximada"},
    "instagram": {"comentarios": True, "tiempo_exacto": True, "guardados": False,
                  "nota": "llegan los que devuelva la API y se ordenan por likes aqui"},
    "tiktok": {"comentarios": False, "tiempo_exacto": False, "guardados": True,
               "nota": "yt-dlp no extrae comentarios de TikTok; a cambio si da el nº de guardados"},
    "web": {"comentarios": False, "tiempo_exacto": False, "guardados": False, "nota": ""},
}

CAMPOS_METRICAS = {
    "vistas": ("view_count", "play_count"),
    "likes": ("like_count", "digg_count"),
    "comentarios": ("comment_count",),
    "compartidos": ("repost_count", "share_count"),
    "guardados": ("save_count", "collect_count"),
}


def soporta_comentarios(plataforma: str) -> bool:
    return bool(CAPACIDADES.get(plataforma, {}).get("comentarios"))


def metricas(info: dict[str, Any]) -> dict[str, int | None]:
    """Saca las metricas a nombres nuestros, mirando los alias de cada extractor."""
    salida: dict[str, int | None] = {}
    for nuestro, alias in CAMPOS_METRICAS.items():
        valor = next((info[a] for a in alias if isinstance(info.get(a), int)), None)
        salida[nuestro] = valor
    return salida


def momento_publicacion(info: dict[str, Any]) -> int | None:
    """Epoch de publicacion. Si solo hay fecha, se toma la medianoche UTC."""
    if isinstance(info.get("timestamp"), int):
        return info["timestamp"]
    fecha = info.get("upload_date")
    if isinstance(fecha, str) and len(fecha) == 8 and fecha.isdigit():
        return int(datetime(int(fecha[:4]), int(fecha[4:6]), int(fecha[6:]),
                            tzinfo=timezone.utc).timestamp())
    return None


def normalizar(crudos: list[dict[str, Any]], plataforma: str,
               publicado_ts: int | None, tope: int = TOPE) -> list[dict[str, Any]]:
    """Se queda con los `tope` comentarios de primer nivel mas votados.

    Se ordena aqui aunque YouTube ya los mande por votos: asi el resultado es el
    mismo venga de donde venga, y las respuestas a otros comentarios se caen.
    """
    exacto = bool(CAPACIDADES.get(plataforma, {}).get("tiempo_exacto"))
    principales = [
        c for c in crudos or []
        if isinstance(c, dict) and (c.get("parent") in (None, "root"))
        and (c.get("text") or "").strip()
    ]
    principales.sort(key=lambda c: (c.get("like_count") or 0), reverse=True)

    salida: list[dict[str, Any]] = []
    for posicion, crudo in enumerate(principales[:tope], 1):
        momento = crudo.get("timestamp")
        momento = momento if isinstance(momento, int) else None
        tras = (momento - publicado_ts) if (momento and publicado_ts) else None
        salida.append({
            "id_externo": str(crudo.get("id") or ""),
            "posicion": posicion,
            "autor": crudo.get("author"),
            "es_del_autor": 1 if crudo.get("author_is_uploader") else 0,
            "texto": (crudo.get("text") or "").strip(),
            "likes": crudo.get("like_count"),
            "publicado_ts": momento,
            # Un comentario nunca es anterior al video: si sale negativo, la
            # estimacion de YouTube se paso de largo y es mejor no guardarla.
            "segundos_tras": tras if (tras is None or tras >= 0) else None,
            "tiempo_exacto": 1 if exacto else 0,
        })
    return salida


def guardar(con: sqlite3.Connection, item_id: int,
            comentarios: list[dict[str, Any]]) -> int:
    con.execute("DELETE FROM comentarios WHERE item_id = ?", (item_id,))
    con.executemany(
        "INSERT INTO comentarios (item_id, id_externo, posicion, autor, es_del_autor,"
        " texto, likes, publicado_ts, segundos_tras, tiempo_exacto)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(item_id, c["id_externo"], c["posicion"], c["autor"], c["es_del_autor"],
          c["texto"], c["likes"], c["publicado_ts"], c["segundos_tras"],
          c["tiempo_exacto"]) for c in comentarios],
    )
    con.commit()
    return len(comentarios)


def guardar_metricas(con: sqlite3.Connection, item_id: int,
                     valores: dict[str, int | None]) -> None:
    """Deja una instantanea y actualiza las columnas de consulta rapida."""
    con.execute(
        "INSERT INTO metricas (item_id, vistas, likes, comentarios, compartidos, guardados)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, valores.get("vistas"), valores.get("likes"),
         valores.get("comentarios"), valores.get("compartidos"), valores.get("guardados")),
    )
    con.execute(
        "UPDATE items SET vistas = ?, likes = ?, comentarios_n = ?, compartidos = ?,"
        " guardados = ? WHERE id = ?",
        (valores.get("vistas"), valores.get("likes"), valores.get("comentarios"),
         valores.get("compartidos"), valores.get("guardados"), item_id),
    )
    con.commit()


def humanizar(segundos: int | None) -> str:
    """'3 h', '5 d', '2 meses': como se lee el retraso de un comentario."""
    if segundos is None:
        return "?"
    if segundos < 3600:
        return f"{segundos // 60} min"
    if segundos < 86400:
        return f"{segundos // 3600} h"
    if segundos < 2592000:
        return f"{segundos // 86400} d"
    if segundos < 31536000:
        return f"{segundos // 2592000} meses"
    return f"{segundos // 31536000} años"
