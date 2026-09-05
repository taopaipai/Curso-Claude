"""Nichos: cada marca con su propio montaje, sus cuentas y su kanban.

Un nicho (marketing, negocio, IA…) es una marca independiente: tiene su publico,
su tono, sus cuentas en cada red y su propio recorrido de montaje. Se llevan por
separado a proposito, para que el estado de uno no se confunda con el de otro.

Los tokens NO se guardan aqui. Cada nicho es un "perfil" y sus credenciales van
en el .env con el sufijo del perfil:

    IG_ACCESS_TOKEN__MARKETING=EAA...
    IG_ACCESS_TOKEN__IA=EAA...

Asi la base de datos se puede copiar o exportar sin llevarse un solo secreto, y
anadir un nicho es anadir variables, no tocar codigo.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

# El recorrido de montaje de un nicho. Es el mismo para todos: por eso se puede
# ver de un vistazo cual va mas adelantado.
ETAPAS: list[dict[str, str]] = [
    {"id": "definicion", "titulo": "Definición",
     "ayuda": "a quién le hablas, qué le prometes y con qué tono"},
    {"id": "marca", "titulo": "Marca",
     "ayuda": "nombre, handle, avatar, bio y aspecto de los vídeos"},
    {"id": "cuentas", "titulo": "Cuentas",
     "ayuda": "crear el perfil en cada red donde vas a publicar"},
    {"id": "acceso", "titulo": "Acceso API",
     "ayuda": "permisos y tokens para que se publique solo"},
    {"id": "contenido", "titulo": "Contenido",
     "ayuda": "el primer lote listo y aprobado"},
    {"id": "activo", "titulo": "Activo",
     "ayuda": "publicando de forma regular"},
]

# El recorrido de cada cuenta, que es distinto del del nicho: una cuenta puede
# estar creada pero sin token, y eso hay que verlo de un vistazo.
ETAPAS_CUENTA = ["sin_crear", "creada", "app", "token", "verificada"]
ETIQUETAS_CUENTA = {
    "sin_crear": "sin crear",
    "creada": "cuenta creada",
    "app": "app dada de alta",
    "token": "token en el .env",
    "verificada": "verificada contra la API",
    "error": "con error",
}

# Plantilla de tareas. Recoge lo que de verdad hay que hacer, incluidos los
# tramites de cada plataforma, que son los que mas tardan.
PLANTILLA: list[tuple[str, str, str]] = [
    ("definicion", "Definir a quién le hablas y qué le prometes",
     "Una frase. Si no cabe en una frase, el nicho es demasiado ancho."),
    ("definicion", "Elegir 3 subtemas y el tono", "Los subtemas son las series de contenido."),
    ("definicion", "Elegir qué colecciones de la bóveda alimentan este nicho",
     "Se filtra por colección en el panel."),
    ("marca", "Elegir nombre y comprobar que el handle está libre en todas las redes",
     "Mejor el mismo handle en todas: se busca una vez y se encuentra siempre."),
    ("marca", "Avatar y bio", "La bio en 150 caracteres, con la promesa por delante."),
    ("marca", "Definir el aspecto del vídeo", "Fondo, tipografía y color de los rótulos."),
    ("cuentas", "Crear el perfil en cada red elegida",
     "Añádelas con 'boveda nicho cuenta <nicho> --red <red> --handle @x'."),
    ("acceso", "Instagram: cuenta profesional y página de Facebook vinculada",
     "Sin esto la API de publicación no existe para tu cuenta."),
    ("acceso", "Instagram: app de Meta y token de larga duración",
     "Permiso instagram_business_content_publish. Va a IG_ACCESS_TOKEN__<NICHO>."),
    ("acceso", "TikTok: app con scope video.publish y solicitar la auditoría",
     "Hasta que auditen la app solo se puede publicar en SELF_ONLY. Tarda semanas: pídela ya."),
    ("acceso", "YouTube: proyecto en Google Cloud y refresh token",
     "Scope youtube.upload. Va a YT_REFRESH_TOKEN__<NICHO>."),
    ("acceso", "Verificar que responden",
     "boveda redes --nicho <nicho> --verificar"),
    ("contenido", "Elegir los primeros 10 guardados de la bóveda",
     "Filtra por colección y ordena por valor histórico."),
    ("contenido", "Aprobar 5 producciones y montar sus vídeos",
     "boveda producir / boveda montar / aprobar desde el panel."),
    ("activo", "Primera publicación", "Empieza con --red archivo si aún faltan permisos."),
    ("activo", "Programar la cola semanal",
     "boveda publicar … --cuando, y un cron con 'boveda cola --confirmar'."),
]


class ErrorNicho(RuntimeError):
    pass


def normalizar_clave(texto: str) -> str:
    clave = re.sub(r"[^a-z0-9]+", "-", (texto or "").strip().lower()).strip("-")
    if not clave:
        raise ErrorNicho("la clave del nicho no puede quedar vacía")
    return clave


def perfil_env(clave: str) -> str:
    """El sufijo que llevan las variables de entorno de este nicho."""
    return re.sub(r"[^A-Z0-9]+", "_", clave.upper()).strip("_")


# --- nichos ------------------------------------------------------------------

def crear(con: sqlite3.Connection, clave: str, nombre: str | None = None,
          descripcion: str | None = None, color: str | None = None) -> int:
    clave = normalizar_clave(clave)
    if con.execute("SELECT 1 FROM nichos WHERE clave = ?", (clave,)).fetchone():
        raise ErrorNicho(f"el nicho '{clave}' ya existe")
    cur = con.execute(
        "INSERT INTO nichos (clave, nombre, descripcion, color) VALUES (?, ?, ?, ?)",
        (clave, nombre or clave.replace("-", " ").title(), descripcion, color),
    )
    nicho_id = int(cur.lastrowid)
    con.executemany(
        "INSERT INTO tareas_nicho (nicho_id, etapa, orden, titulo, detalle)"
        " VALUES (?, ?, ?, ?, ?)",
        [(nicho_id, etapa, orden, titulo, detalle)
         for orden, (etapa, titulo, detalle) in enumerate(PLANTILLA)],
    )
    con.commit()
    return nicho_id


def obtener(con: sqlite3.Connection, clave: str) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM nichos WHERE clave = ?",
                       (normalizar_clave(clave),)).fetchone()
    if fila is None:
        raise ErrorNicho(f"no existe el nicho '{clave}'")
    return fila


def listar(con: sqlite3.Connection) -> list[dict[str, Any]]:
    salida = []
    for fila in con.execute("SELECT * FROM nichos ORDER BY clave"):
        salida.append({**dict(fila), **progreso(con, int(fila["id"]))})
    return salida


def progreso(con: sqlite3.Connection, nicho_id: int) -> dict[str, Any]:
    """Cuanto lleva montado el nicho y donde esta atascado."""
    tareas = con.execute(
        "SELECT etapa, hecha FROM tareas_nicho WHERE nicho_id = ?", (nicho_id,)).fetchall()
    hechas = sum(1 for t in tareas if t["hecha"])
    por_etapa = {e["id"]: {"total": 0, "hechas": 0} for e in ETAPAS}
    for tarea in tareas:
        casilla = por_etapa.setdefault(tarea["etapa"], {"total": 0, "hechas": 0})
        casilla["total"] += 1
        casilla["hechas"] += 1 if tarea["hecha"] else 0

    # La etapa actual es la primera que aun no esta terminada.
    actual = "activo"
    for etapa in ETAPAS:
        casilla = por_etapa.get(etapa["id"], {"total": 0, "hechas": 0})
        if casilla["total"] and casilla["hechas"] < casilla["total"]:
            actual = etapa["id"]
            break

    cuentas = con.execute(
        "SELECT red, etapa, handle FROM cuentas WHERE nicho_id = ? ORDER BY red",
        (nicho_id,)).fetchall()
    return {
        "tareas_totales": len(tareas), "tareas_hechas": hechas,
        "porcentaje": round(100 * hechas / len(tareas)) if tareas else 0,
        "etapa_actual": actual, "por_etapa": por_etapa,
        "cuentas": [dict(c) for c in cuentas],
        "listas": sum(1 for c in cuentas if c["etapa"] == "verificada"),
    }


def actualizar(con: sqlite3.Connection, clave: str, **campos: Any) -> None:
    permitidos = {"nombre", "descripcion", "publico", "promesa", "tono", "color", "etapa"}
    campos = {k: v for k, v in campos.items() if k in permitidos and v is not None}
    if not campos:
        return
    nicho = obtener(con, clave)
    asignaciones = ", ".join(f"{k} = ?" for k in campos)
    con.execute(f"UPDATE nichos SET {asignaciones} WHERE id = ?",
                [*campos.values(), nicho["id"]])
    con.commit()


def borrar(con: sqlite3.Connection, clave: str) -> None:
    nicho = obtener(con, clave)
    con.execute("DELETE FROM nichos WHERE id = ?", (nicho["id"],))
    con.commit()


# --- tareas ------------------------------------------------------------------

def tareas(con: sqlite3.Connection, nicho_id: int) -> list[dict[str, Any]]:
    return [dict(f) for f in con.execute(
        "SELECT * FROM tareas_nicho WHERE nicho_id = ? ORDER BY orden, id", (nicho_id,))]


def marcar_tarea(con: sqlite3.Connection, tarea_id: int, hecha: bool = True) -> None:
    cur = con.execute(
        "UPDATE tareas_nicho SET hecha = ?,"
        " completado_en = CASE WHEN ? = 1 THEN datetime('now') ELSE NULL END"
        " WHERE id = ?",
        (1 if hecha else 0, 1 if hecha else 0, tarea_id),
    )
    if not cur.rowcount:
        raise ErrorNicho(f"no existe la tarea {tarea_id}")
    con.commit()


def anadir_tarea(con: sqlite3.Connection, nicho_id: int, etapa: str, titulo: str,
                 detalle: str | None = None, red: str | None = None) -> int:
    if etapa not in {e["id"] for e in ETAPAS}:
        raise ErrorNicho(f"etapa desconocida: {etapa}")
    orden = con.execute(
        "SELECT COALESCE(MAX(orden), 0) + 1 n FROM tareas_nicho WHERE nicho_id = ?",
        (nicho_id,)).fetchone()["n"]
    cur = con.execute(
        "INSERT INTO tareas_nicho (nicho_id, etapa, orden, titulo, detalle, red)"
        " VALUES (?, ?, ?, ?, ?, ?)", (nicho_id, etapa, orden, titulo, detalle, red))
    con.commit()
    return int(cur.lastrowid)


# --- cuentas -----------------------------------------------------------------

def cuentas(con: sqlite3.Connection, nicho_id: int) -> list[dict[str, Any]]:
    return [dict(f) for f in con.execute(
        "SELECT * FROM cuentas WHERE nicho_id = ? ORDER BY red", (nicho_id,))]


def anadir_cuenta(con: sqlite3.Connection, clave: str, red: str, handle: str | None = None,
                  url: str | None = None, estrategia: str | None = None,
                  etapa: str = "creada") -> int:
    from .publish import REDES
    if red not in REDES:
        raise ErrorNicho(f"red desconocida: {red}. Opciones: {', '.join(sorted(REDES))}")
    if etapa not in ETAPAS_CUENTA and etapa != "error":
        raise ErrorNicho(f"etapa de cuenta desconocida: {etapa}")

    nicho = obtener(con, clave)
    cur = con.execute(
        "INSERT INTO cuentas (nicho_id, red, handle, url, estrategia, etapa)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(nicho_id, red) DO UPDATE SET"
        "   handle = COALESCE(excluded.handle, handle),"
        "   url = COALESCE(excluded.url, url),"
        "   estrategia = COALESCE(excluded.estrategia, estrategia),"
        "   etapa = excluded.etapa",
        (nicho["id"], red, handle, url, estrategia, etapa),
    )
    con.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    return int(con.execute("SELECT id FROM cuentas WHERE nicho_id = ? AND red = ?",
                           (nicho["id"], red)).fetchone()["id"])


def mover_cuenta(con: sqlite3.Connection, cuenta_id: int, etapa: str,
                 notas: str | None = None) -> None:
    if etapa not in ETAPAS_CUENTA and etapa != "error":
        raise ErrorNicho(f"etapa de cuenta desconocida: {etapa}")
    cur = con.execute(
        "UPDATE cuentas SET etapa = ?, notas = COALESCE(?, notas),"
        " verificado_en = CASE WHEN ? = 'verificada' THEN datetime('now') ELSE verificado_en END"
        " WHERE id = ?", (etapa, notas, etapa, cuenta_id))
    if not cur.rowcount:
        raise ErrorNicho(f"no existe la cuenta {cuenta_id}")
    con.commit()


def borrar_cuenta(con: sqlite3.Connection, cuenta_id: int) -> None:
    con.execute("DELETE FROM cuentas WHERE id = ?", (cuenta_id,))
    con.commit()


def redes_publicables(con: sqlite3.Connection, clave: str) -> list[sqlite3.Row]:
    """Las cuentas del nicho listas para recibir una publicacion."""
    nicho = obtener(con, clave)
    return con.execute(
        "SELECT * FROM cuentas WHERE nicho_id = ? AND etapa IN ('token', 'verificada')"
        " ORDER BY red", (nicho["id"],)).fetchall()
