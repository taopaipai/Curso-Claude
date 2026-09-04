"""Convierte una pieza analizada en contenido nuestro, listo para publicar."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import Config
from .analyze import _crear_mensaje, _texto_respuesta, cliente

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "repurpose.md"

INSTRUCCIONES = {
    "reel": ("Guion de reel vertical de 30-45 s. Formato: bloques con timestamp, "
             "lo que se dice, el texto en pantalla y la indicacion visual."),
    "short": ("Guion de short de 20-30 s, mas rapido y con un solo remate. "
              "Mismo formato de bloques que el reel."),
    "carrusel": ("Carrusel de 7-10 laminas. Una lamina por bloque: titular corto "
                 "(max 8 palabras) y cuerpo de maximo 40 palabras. Ultima lamina con CTA."),
    "hilo": ("Hilo de 6-10 publicaciones. Cada una debe sostenerse sola y encadenar "
             "con la siguiente. Maximo 280 caracteres por publicacion."),
    "newsletter": ("Seccion de newsletter de 400-600 palabras: asunto, entrada, "
                   "desarrollo con subtitulos y cierre accionable."),
    "blog": ("Articulo de 700-900 palabras con titulo, entradilla, subtitulos "
             "y conclusion accionable."),
}


def _material(con: sqlite3.Connection, item_id: int) -> tuple[sqlite3.Row, dict[str, Any]]:
    fila = con.execute(
        """
        SELECT i.*, a.analisis_json, t.texto AS transcripcion
        FROM items i
        JOIN analisis a       ON a.item_id = i.id
        LEFT JOIN transcripciones t ON t.item_id = i.id
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()
    if fila is None:
        raise LookupError(f"el item {item_id} no existe o todavia no esta analizado")
    return fila, json.loads(fila["analisis_json"])


def generar(cfg: Config, con: sqlite3.Connection, item_id: int, formato: str,
            nicho: str | None = None, notas: str | None = None, cli=None) -> int:
    if formato not in INSTRUCCIONES:
        raise ValueError(f"formato desconocido: {formato}. Opciones: {', '.join(INSTRUCCIONES)}")

    fila, analisis = _material(con, item_id)
    cli = cli or cliente()

    contenido = "\n\n".join([
        f"FORMATO PEDIDO: {formato}\n{INSTRUCCIONES[formato]}",
        f"NICHO OBJETIVO: {nicho or analisis.get('nicho') or 'el mismo de la pieza original'}",
        f"NOTAS DEL EQUIPO: {notas or '(ninguna)'}",
        f"ANALISIS DE LA PIEZA ORIGINAL:\n{json.dumps(analisis, ensure_ascii=False, indent=2)}",
        f"TRANSCRIPCION ORIGINAL (solo como referencia, no copiar):\n"
        f"{(fila['transcripcion'] or '')[:8000]}",
    ])

    respuesta = _crear_mensaje(cli, cfg, PROMPT.read_text(encoding="utf-8"), contenido, None)
    cuerpo = _texto_respuesta(respuesta).strip()
    titulo = next((l.lstrip("# ").strip() for l in cuerpo.splitlines() if l.strip()), formato)

    cur = con.execute(
        "INSERT INTO producciones (item_id, formato, nicho, titulo, cuerpo, modelo) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, formato, nicho, titulo[:200], cuerpo, cfg.modelo),
    )
    con.commit()
    return int(cur.lastrowid)
