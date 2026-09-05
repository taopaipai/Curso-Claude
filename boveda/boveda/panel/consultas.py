"""Las consultas que alimentan el tablero. Solo SQL: nada de HTTP aqui."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .. import nichos

# Las columnas del kanban son las etapas reales del pipeline. Las cuatro
# primeras son items (la publicacion guardada); las cuatro ultimas son
# producciones (lo nuestro, derivado de ese item). Es el mismo recorrido que
# hace el material: entra como favorito y sale como publicacion nuestra.
COLUMNAS: list[dict[str, str]] = [
    {"id": "importado", "titulo": "Registrado", "tipo": "item",
     "ayuda": "guardado detectado; falta bajar audio y metadatos"},
    {"id": "descargado", "titulo": "Descargado", "tipo": "item",
     "ayuda": "audio, ficha, metricas y comentarios ya capturados"},
    {"id": "transcrito", "titulo": "Transcrito", "tipo": "item",
     "ayuda": "con transcripcion y, si hacia falta, texto en pantalla"},
    {"id": "analizado", "titulo": "Analizado", "tipo": "item",
     "ayuda": "gancho, estructura y vigencia; listo para producir"},
    {"id": "borrador", "titulo": "Borrador", "tipo": "produccion",
     "ayuda": "guion generado, pendiente de tu visto bueno"},
    {"id": "aprobado", "titulo": "Aprobado", "tipo": "produccion",
     "ayuda": "aprobado por ti; monta el video y programalo"},
    {"id": "programada", "titulo": "En cola", "tipo": "publicacion",
     "ayuda": "esperando su turno para salir"},
    {"id": "publicado", "titulo": "Publicado", "tipo": "produccion",
     "ayuda": "ya salio en al menos una red"},
]

ESTADOS_ERROR = ("error_descarga", "error_transcripcion", "error_analisis")


def _metricas(fila: sqlite3.Row) -> dict[str, Any]:
    return {"vistas": fila["vistas"], "likes": fila["likes"],
            "comentarios": fila["comentarios_n"], "guardados": fila["guardados"]}


def tarjetas_items(con: sqlite3.Connection, estados: tuple[str, ...],
                   filtros: dict[str, Any], limite: int = 60) -> list[dict[str, Any]]:
    sql = """
        SELECT i.*, o.carpeta, a.tipo_contenido, a.nicho, a.vigencia_estado,
               a.valor_historico, a.hook_texto,
               (SELECT COUNT(*) FROM comentarios c WHERE c.item_id = i.id) n_comentarios,
               (SELECT ruta FROM fotogramas f WHERE f.item_id = i.id ORDER BY indice LIMIT 1) fotograma
        FROM items i
        LEFT JOIN origenes o ON o.id = i.origen_id
        LEFT JOIN analisis a ON a.item_id = i.id
        WHERE i.estado IN ({})
    """.format(", ".join("?" * len(estados)))
    args: list[Any] = list(estados)

    if filtros.get("plataforma"):
        sql += " AND i.plataforma = ?"
        args.append(filtros["plataforma"])
    if filtros.get("carpeta"):
        sql += " AND o.carpeta = ?"
        args.append(filtros["carpeta"])
    if filtros.get("q"):
        sql += " AND (i.titulo LIKE ? OR i.autor LIKE ?)"
        args += [f"%{filtros['q']}%"] * 2
    sql += " ORDER BY i.actualizado_en DESC LIMIT ?"
    args.append(limite)

    tarjetas = []
    for fila in con.execute(sql, args):
        tarjetas.append({
            "tipo": "item", "id": fila["id"], "titulo": fila["titulo"] or fila["url_canonica"],
            "subtitulo": fila["autor"] or "", "plataforma": fila["plataforma"],
            "carpeta": fila["carpeta"], "url": fila["url_canonica"],
            "metricas": _metricas(fila), "n_comentarios": fila["n_comentarios"],
            "nicho": fila["nicho"], "tipo_contenido": fila["tipo_contenido"],
            "vigencia": fila["vigencia_estado"], "valor": fila["valor_historico"],
            "extra": fila["hook_texto"] or "", "error": fila["error"],
            "miniatura": f"/miniatura/{fila['id']}" if fila["fotograma"] else None,
        })
    return tarjetas


def tarjetas_producciones(con: sqlite3.Connection, estados: tuple[str, ...],
                          filtros: dict[str, Any], limite: int = 60) -> list[dict[str, Any]]:
    sql = """
        SELECT p.*, i.plataforma, i.titulo AS titulo_item, o.carpeta,
               (SELECT COUNT(*) FROM montajes m WHERE m.produccion_id = p.id) montado,
               (SELECT GROUP_CONCAT(red) FROM publicaciones pu
                 WHERE pu.produccion_id = p.id AND pu.estado = 'publicada') redes,
               (SELECT COUNT(*) FROM fotogramas f WHERE f.item_id = i.id) tiene_foto
        FROM producciones p
        JOIN items i ON i.id = p.item_id
        LEFT JOIN origenes o ON o.id = i.origen_id
        WHERE p.estado IN ({})
    """.format(", ".join("?" * len(estados)))
    args: list[Any] = list(estados)

    if filtros.get("plataforma"):
        sql += " AND i.plataforma = ?"
        args.append(filtros["plataforma"])
    if filtros.get("carpeta"):
        sql += " AND o.carpeta = ?"
        args.append(filtros["carpeta"])
    if filtros.get("q"):
        sql += " AND (p.titulo LIKE ? OR p.cuerpo LIKE ?)"
        args += [f"%{filtros['q']}%"] * 2
    sql += " ORDER BY p.creado_en DESC LIMIT ?"
    args.append(limite)

    tarjetas = []
    for fila in con.execute(sql, args):
        tarjetas.append({
            "tipo": "produccion", "id": fila["id"], "item_id": fila["item_id"],
            "titulo": fila["titulo"] or fila["formato"],
            "subtitulo": f"{fila['formato']} · {fila['nicho'] or 'sin nicho'}",
            "plataforma": fila["plataforma"], "carpeta": fila["carpeta"],
            "formato": fila["formato"], "nicho": fila["nicho"],
            "montado": bool(fila["montado"]), "redes": fila["redes"] or "",
            "extra": (fila["cuerpo"] or "")[:160],
            "miniatura": f"/miniatura/{fila['item_id']}" if fila["tiene_foto"] else None,
        })
    return tarjetas


def tarjetas_cola(con: sqlite3.Connection, filtros: dict[str, Any]) -> list[dict[str, Any]]:
    filas = con.execute(
        """
        SELECT pu.*, p.formato, p.titulo, p.nicho, p.item_id,
               (SELECT COUNT(*) FROM fotogramas f WHERE f.item_id = p.item_id) tiene_foto
        FROM publicaciones pu
        JOIN producciones p ON p.id = pu.produccion_id
        WHERE pu.estado = 'programada'
        ORDER BY COALESCE(pu.programado_para, pu.creado_en)
        """
    ).fetchall()
    return [{
        "tipo": "publicacion", "id": fila["id"], "item_id": fila["item_id"],
        "produccion_id": fila["produccion_id"],
        "titulo": fila["titulo"] or fila["formato"],
        "subtitulo": f"{fila['red']} · {fila['programado_para'] or 'en cuanto toque'}",
        "formato": fila["formato"], "nicho": fila["nicho"], "red": fila["red"],
        "extra": "",
        "miniatura": f"/miniatura/{fila['item_id']}" if fila["tiene_foto"] else None,
    } for fila in filas]


def tablero(con: sqlite3.Connection, filtros: dict[str, Any]) -> dict[str, Any]:
    columnas = []
    for columna in COLUMNAS:
        if columna["tipo"] == "item":
            tarjetas = tarjetas_items(con, (columna["id"],), filtros)
        elif columna["tipo"] == "publicacion":
            tarjetas = tarjetas_cola(con, filtros)
        else:
            tarjetas = tarjetas_producciones(con, (columna["id"],), filtros)
        columnas.append({**columna, "tarjetas": tarjetas, "total": len(tarjetas)})

    errores = tarjetas_items(con, ESTADOS_ERROR, filtros)
    if errores:
        columnas.append({"id": "errores", "titulo": "Con error", "tipo": "item",
                         "ayuda": "algo fallo; se puede reintentar",
                         "tarjetas": errores, "total": len(errores)})
    return {"columnas": columnas, "filtros": opciones_de_filtro(con)}


def opciones_de_filtro(con: sqlite3.Connection) -> dict[str, list[str]]:
    return {
        "plataformas": [f["plataforma"] for f in con.execute(
            "SELECT DISTINCT plataforma FROM items ORDER BY 1")],
        "carpetas": [f["carpeta"] for f in con.execute(
            "SELECT DISTINCT carpeta FROM origenes WHERE carpeta IS NOT NULL ORDER BY 1")],
    }


def detalle(con: sqlite3.Connection, item_id: int) -> dict[str, Any] | None:
    item = con.execute(
        """
        SELECT i.*, o.carpeta, a.analisis_json, t.texto AS transcripcion,
               oc.texto AS pantalla
        FROM items i
        LEFT JOIN origenes o ON o.id = i.origen_id
        LEFT JOIN analisis a ON a.item_id = i.id
        LEFT JOIN transcripciones t ON t.item_id = i.id
        LEFT JOIN ocr oc ON oc.item_id = i.id
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()
    if item is None:
        return None

    comentarios = [dict(f) for f in con.execute(
        "SELECT posicion, autor, texto, likes, segundos_tras, tiempo_exacto, es_del_autor "
        "FROM comentarios WHERE item_id = ? ORDER BY posicion", (item_id,))]
    historial = [dict(f) for f in con.execute(
        "SELECT capturado_en, vistas, likes, comentarios, guardados FROM metricas "
        "WHERE item_id = ? ORDER BY capturado_en", (item_id,))]
    producciones = [dict(f) for f in con.execute(
        "SELECT id, formato, nicho, estado, titulo, cuerpo FROM producciones "
        "WHERE item_id = ? ORDER BY id DESC", (item_id,))]
    publicaciones = [dict(f) for f in con.execute(
        "SELECT pu.id, pu.red, pu.estado, pu.url_remota, pu.programado_para, pu.error,"
        " pu.produccion_id FROM publicaciones pu JOIN producciones p ON p.id = pu.produccion_id"
        " WHERE p.item_id = ? ORDER BY pu.id DESC", (item_id,))]

    return {
        "item": {k: item[k] for k in item.keys() if k != "crudo_json"},
        "analisis": json.loads(item["analisis_json"]) if item["analisis_json"] else None,
        "comentarios": comentarios,
        "historial": historial,
        "producciones": producciones,
        "publicaciones": publicaciones,
    }


def resumen(con: sqlite3.Connection) -> dict[str, Any]:
    total = con.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
    analizados = con.execute("SELECT COUNT(*) n FROM analisis").fetchone()["n"]
    publicados = con.execute(
        "SELECT COUNT(*) n FROM publicaciones WHERE estado = 'publicada'").fetchone()["n"]
    comentarios = con.execute("SELECT COUNT(*) n FROM comentarios").fetchone()["n"]
    return {"items": total, "analizados": analizados, "publicados": publicados,
            "comentarios": comentarios}


def tablero_nicho(con: sqlite3.Connection, clave: str) -> dict[str, Any]:
    """El kanban de montaje de UN nicho: sus etapas, sus tareas y sus cuentas.

    Cada nicho tiene su propio tablero a proposito: es lo que evita que el
    estado de marketing se confunda con el de IA.
    """
    nicho = nichos.obtener(con, clave)
    nicho_id = int(nicho["id"])
    tareas = nichos.tareas(con, nicho_id)

    columnas = []
    for etapa in nichos.ETAPAS:
        de_esta = [t for t in tareas if t["etapa"] == etapa["id"]]
        columnas.append({**etapa, "tareas": de_esta,
                         "total": len(de_esta),
                         "hechas": sum(1 for t in de_esta if t["hecha"])})

    return {
        "nicho": dict(nicho),
        "perfil": nichos.perfil_env(nicho["clave"]),
        "columnas": columnas,
        "cuentas": nichos.cuentas(con, nicho_id),
        "escalera": nichos.ETAPAS_CUENTA,
        "etiquetas_cuenta": nichos.ETIQUETAS_CUENTA,
        "progreso": nichos.progreso(con, nicho_id),
        "producciones": [dict(f) for f in con.execute(
            "SELECT id, formato, estado, titulo FROM producciones WHERE nicho = ?"
            " ORDER BY id DESC LIMIT 20", (nicho["clave"],))],
    }
