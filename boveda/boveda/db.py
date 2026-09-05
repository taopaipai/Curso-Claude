"""Acceso a SQLite: apertura, migracion inicial e indice de busqueda."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

ESQUEMA = Path(__file__).resolve().parent.parent / "schema.sql"


def conectar(ruta: Path) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# Columnas anadidas despues de la primera version. Las tablas nuevas las crea
# solo el CREATE TABLE IF NOT EXISTS, pero una columna nueva en una tabla que ya
# existe hay que anadirla a mano.
COLUMNAS_NUEVAS: dict[str, dict[str, str]] = {
    "items": {
        "publicado_ts": "INTEGER",
        "vistas": "INTEGER",
        "likes": "INTEGER",
        "comentarios_n": "INTEGER",
        "compartidos": "INTEGER",
        "guardados": "INTEGER",
    },
    "publicaciones": {"cuenta_id": "INTEGER"},
    "producciones": {"nicho_id": "INTEGER"},
}


def inicializar(con: sqlite3.Connection) -> None:
    con.executescript(ESQUEMA.read_text(encoding="utf-8"))
    for tabla, columnas in COLUMNAS_NUEVAS.items():
        existentes = {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")}
        for nombre, tipo in columnas.items():
            if nombre not in existentes:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")
    con.commit()


def obtener_origen(con: sqlite3.Connection, plataforma: str, carpeta: str | None,
                   descripcion: str | None = None) -> int:
    cur = con.execute(
        "SELECT id FROM origenes WHERE plataforma = ? AND carpeta IS ?",
        (plataforma, carpeta),
    )
    fila = cur.fetchone()
    if fila:
        return int(fila["id"])
    cur = con.execute(
        "INSERT INTO origenes (plataforma, carpeta, descripcion) VALUES (?, ?, ?)",
        (plataforma, carpeta, descripcion),
    )
    return int(cur.lastrowid)


def insertar_item(con: sqlite3.Connection, item: dict[str, Any]) -> tuple[int, bool]:
    """Inserta un guardado. Devuelve (item_id, era_nuevo).

    La deduplicacion es por `url_canonica`: reimportar el mismo export no duplica
    nada y no pisa el trabajo ya hecho (transcripcion, analisis).
    """
    columnas = (
        "origen_id", "plataforma", "url_canonica", "id_externo", "autor", "titulo",
        "descripcion", "duracion_seg", "publicado_en", "guardado_en", "idioma",
        "metricas_json", "crudo_json",
    )
    valores = [item.get(c) for c in columnas]
    cur = con.execute(
        f"INSERT OR IGNORE INTO items ({', '.join(columnas)}) "
        f"VALUES ({', '.join('?' * len(columnas))})",
        valores,
    )
    if cur.rowcount:
        return int(cur.lastrowid), True
    fila = con.execute(
        "SELECT id FROM items WHERE url_canonica = ?", (item["url_canonica"],)
    ).fetchone()
    return int(fila["id"]), False


def marcar(con: sqlite3.Connection, item_id: int, estado: str,
           error: str | None = None, **campos: Any) -> None:
    sets = ["estado = ?", "error = ?", "actualizado_en = datetime('now')"]
    valores: list[Any] = [estado, error]
    for clave, valor in campos.items():
        sets.append(f"{clave} = ?")
        valores.append(valor)
    valores.append(item_id)
    con.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", valores)
    con.commit()


def pendientes(con: sqlite3.Connection, estado: str, limite: int | None = None,
               plataforma: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM items WHERE estado = ?"
    args: list[Any] = [estado]
    if plataforma:
        sql += " AND plataforma = ?"
        args.append(plataforma)
    sql += " ORDER BY id"
    if limite:
        sql += f" LIMIT {int(limite)}"
    return con.execute(sql, args).fetchall()


def reindexar(con: sqlite3.Connection, item_id: int) -> None:
    """Reconstruye la fila FTS de un item con su titulo, transcripcion y analisis."""
    fila = con.execute(
        """
        SELECT i.titulo, i.autor, i.descripcion,
               t.texto AS transcripcion, o.texto AS pantalla, a.analisis_json
        FROM items i
        LEFT JOIN transcripciones t ON t.item_id = i.id
        LEFT JOIN ocr o             ON o.item_id = i.id
        LEFT JOIN analisis a        ON a.item_id = i.id
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()
    if fila is None:
        return
    con.execute("DELETE FROM busqueda WHERE item_id = ?", (item_id,))
    con.execute(
        "INSERT INTO busqueda (titulo, autor, transcripcion, analisis, item_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            fila["titulo"] or "",
            fila["autor"] or "",
            "\n".join(t for t in (fila["transcripcion"], fila["pantalla"],
                                   fila["descripcion"]) if t),
            _texto_plano(fila["analisis_json"]),
            item_id,
        ),
    )
    con.commit()


def _texto_plano(analisis_json: str | None) -> str:
    """Aplana el JSON de analisis a texto para que FTS pueda indexarlo."""
    if not analisis_json:
        return ""
    try:
        datos = json.loads(analisis_json)
    except json.JSONDecodeError:
        return analisis_json

    trozos: list[str] = []

    def recorrer(nodo: Any) -> None:
        if isinstance(nodo, dict):
            for valor in nodo.values():
                recorrer(valor)
        elif isinstance(nodo, list):
            for valor in nodo:
                recorrer(valor)
        elif isinstance(nodo, str):
            trozos.append(nodo)

    recorrer(datos)
    return "\n".join(trozos)


def buscar(con: sqlite3.Connection, consulta: str, limite: int = 20) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT b.item_id, i.plataforma, i.autor, i.titulo, i.url_canonica,
               a.tipo_contenido, a.nicho, a.vigencia_estado,
               snippet(busqueda, 2, '[', ']', ' ... ', 18) AS extracto
        FROM busqueda b
        JOIN items i    ON i.id = b.item_id
        LEFT JOIN analisis a ON a.item_id = b.item_id
        WHERE busqueda MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (consulta, limite),
    ).fetchall()


def etiquetar(con: sqlite3.Connection, item_id: int, etiquetas: Iterable[str]) -> None:
    con.executemany(
        "INSERT OR IGNORE INTO etiquetas (item_id, etiqueta) VALUES (?, ?)",
        [(item_id, e.strip().lower()) for e in etiquetas if e and e.strip()],
    )
    con.commit()
