"""Analiza cada pieza con Claude y guarda un objeto estructurado.

Se usa structured outputs (`output_config.format`) para que la respuesta sea
siempre JSON valido contra el esquema: sin eso, parsear miles de analisis a mano
se convierte en el cuello de botella.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import Config
from .. import db

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "analisis.md")

# Esquema del analisis. `additionalProperties: false` + `required` completos son
# obligatorios para structured outputs.
ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tipo_contenido": {
            "type": "string",
            "enum": ["viral", "instructivo", "noticia", "opinion", "promocional", "otro"],
        },
        "nicho": {"type": "string"},
        "tema_principal": {"type": "string"},
        "subtemas": {"type": "array", "items": {"type": "string"}},
        "hook": {
            "type": "object",
            "properties": {
                "texto": {"type": "string"},
                "tecnica": {"type": "string"},
                "segundos": {"type": "number"},
                "por_que_funciona": {"type": "string"},
            },
            "required": ["texto", "tecnica", "segundos", "por_que_funciona"],
            "additionalProperties": False,
        },
        "estructura": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "seccion": {"type": "string"},
                    "inicio_seg": {"type": "number"},
                    "fin_seg": {"type": "number"},
                    "proposito": {"type": "string"},
                    "texto_clave": {"type": "string"},
                },
                "required": ["seccion", "inicio_seg", "fin_seg", "proposito", "texto_clave"],
                "additionalProperties": False,
            },
        },
        "por_que_funciona": {
            "type": "object",
            "properties": {
                "factores": {"type": "array", "items": {"type": "string"}},
                "emocion_dominante": {"type": "string"},
                "promesa": {"type": "string"},
                "tension": {"type": "string"},
                "resolucion": {"type": "string"},
            },
            "required": ["factores", "emocion_dominante", "promesa", "tension", "resolucion"],
            "additionalProperties": False,
        },
        "ganchos_reutilizables": {"type": "array", "items": {"type": "string"}},
        "datos_y_afirmaciones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "afirmacion": {"type": "string"},
                    "verificable": {"type": "boolean"},
                    "contexto_temporal": {"type": "string"},
                },
                "required": ["afirmacion", "verificable", "contexto_temporal"],
                "additionalProperties": False,
            },
        },
        "vigencia": {
            "type": "object",
            "properties": {
                "estado": {"type": "string", "enum": ["vigente", "caducado", "atemporal"]},
                "razon": {"type": "string"},
                "valor_historico": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["estado", "razon", "valor_historico"],
            "additionalProperties": False,
        },
        "aplicabilidad": {
            "type": "object",
            "properties": {
                "para_nosotros": {"type": "array", "items": {"type": "string"}},
                "para_ensenar": {"type": "array", "items": {"type": "string"}},
                "formatos_sugeridos": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["reel", "carrusel", "hilo", "newsletter", "short", "blog"],
                    },
                },
            },
            "required": ["para_nosotros", "para_ensenar", "formatos_sugeridos"],
            "additionalProperties": False,
        },
        "cta": {"type": "string"},
        "etiquetas": {"type": "array", "items": {"type": "string"}},
        "calidad_transcripcion": {
            "type": "string",
            "enum": ["buena", "regular", "mala"],
        },
    },
    "required": [
        "tipo_contenido", "nicho", "tema_principal", "subtemas", "hook", "estructura",
        "por_que_funciona", "ganchos_reutilizables", "datos_y_afirmaciones", "vigencia",
        "aplicabilidad", "cta", "etiquetas", "calidad_transcripcion",
    ],
    "additionalProperties": False,
}

# La API rechaza la peticion si el texto no cabe; recortamos con aviso explicito
# en vez de truncar en silencio.
LIMITE_TRANSCRIPCION = 120_000


def cliente():
    import anthropic
    return anthropic.Anthropic()


def _crear_mensaje(cli, cfg: Config, prompt_sistema: str,
                   contenido: str | list[dict[str, Any]],
                   esquema: dict[str, Any] | None) -> Any:
    """Llama a Claude con fallback por rechazo activado (y sin el si no esta disponible)."""
    kwargs: dict[str, Any] = {
        "model": cfg.modelo,
        "max_tokens": 16000,
        "system": [{"type": "text", "text": prompt_sistema,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": contenido}],
        "thinking": {"type": "adaptive"},
    }
    if esquema is not None:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": esquema}}

    try:
        return cli.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    except _errores_sin_fallback():
        # El fallback por rechazo no esta habilitado en esta cuenta: seguimos sin el.
        return cli.messages.create(**kwargs)


def _errores_sin_fallback() -> tuple[type[BaseException], ...]:
    """Errores que significan "esta cuenta no tiene el fallback por rechazo"."""
    try:
        import anthropic
    except ImportError:
        return (TypeError,)
    return (anthropic.BadRequestError, TypeError)


def texto_disponible(con: sqlite3.Connection, item_id: int) -> tuple[str, str]:
    """Devuelve (transcripcion, texto_en_pantalla) de un item."""
    voz = con.execute(
        "SELECT texto FROM transcripciones WHERE item_id = ?", (item_id,)
    ).fetchone()
    pantalla = con.execute("SELECT texto FROM ocr WHERE item_id = ?", (item_id,)).fetchone()
    return (voz["texto"] if voz else "") or "", (pantalla["texto"] if pantalla else "") or ""


def _contexto(con: sqlite3.Connection, item: sqlite3.Row) -> str:
    fila = con.execute(
        "SELECT texto, segmentos_json FROM transcripciones WHERE item_id = ?", (item["id"],)
    ).fetchone()
    texto = (fila["texto"] if fila else "") or ""
    aviso = ""
    if len(texto) > LIMITE_TRANSCRIPCION:
        texto = texto[:LIMITE_TRANSCRIPCION]
        aviso = "\n[AVISO: transcripcion recortada por longitud]"

    segmentos = json.loads(fila["segmentos_json"]) if fila and fila["segmentos_json"] else []
    linea_tiempo = "\n".join(
        f'{s["inicio"]:.1f}-{s["fin"]:.1f}s: {s["texto"]}' for s in segmentos[:400]
    )

    partes = [
        f"PLATAFORMA: {item['plataforma']}",
        f"AUTOR: {item['autor'] or 'desconocido'}",
        f"TITULO: {item['titulo'] or '(sin titulo)'}",
        f"PUBLICADO: {item['publicado_en'] or 'desconocido'}",
        f"GUARDADO POR EL USUARIO: {item['guardado_en'] or 'desconocido'}",
        f"DURACION: {item['duracion_seg'] or '?'} s",
        f"METRICAS: {item['metricas_json'] or '{}'}",
        f"DESCRIPCION/CAPTION:\n{(item['descripcion'] or '')[:4000]}",
        f"TRANSCRIPCION (voz):\n{texto or '(el video no tiene voz)'}{aviso}",
    ]
    if linea_tiempo:
        partes.append(f"SEGMENTOS CON TIEMPO:\n{linea_tiempo}")

    pantalla = con.execute(
        "SELECT texto, motor FROM ocr WHERE item_id = ?", (item["id"],)
    ).fetchone()
    if pantalla and pantalla["texto"]:
        partes.append(
            f"TEXTO EN PANTALLA (leido de los fotogramas con {pantalla['motor']}):\n"
            f"{pantalla['texto'][:LIMITE_TRANSCRIPCION]}"
        )
        rotulos = con.execute(
            "SELECT segundo, texto, descripcion FROM fotogramas "
            "WHERE item_id = ? AND (texto <> '' OR descripcion <> '') ORDER BY indice",
            (item["id"],),
        ).fetchall()
        if rotulos:
            partes.append("FOTOGRAMAS CON TIEMPO:\n" + "\n".join(
                f"{(f['segundo'] or 0):.1f}s: {(f['texto'] or '').strip()}"
                + (f"  [visual: {f['descripcion']}]" if f["descripcion"] else "")
                for f in rotulos
            ))
        if not texto:
            partes.append(
                "NOTA: esta pieza no tiene voz. Todo el mensaje esta en el texto en "
                "pantalla; analiza el gancho y la estructura sobre ese texto y sobre "
                "el ritmo con que aparecen los rotulos."
            )
    return "\n\n".join(partes)


def _texto_respuesta(respuesta: Any) -> str:
    if getattr(respuesta, "stop_reason", None) == "refusal":
        detalle = getattr(respuesta, "stop_details", None)
        raise RuntimeError(f"la API rechazo la peticion ({getattr(detalle, 'category', '?')})")
    for bloque in respuesta.content:
        if bloque.type == "text":
            return bloque.text
    raise RuntimeError("la respuesta no trae bloque de texto")


def procesar(cfg: Config, con: sqlite3.Connection, item: sqlite3.Row, cli=None) -> None:
    cli = cli or cliente()
    voz, pantalla = texto_disponible(con, item["id"])
    if not voz.strip() and not pantalla.strip():
        db.marcar(con, item["id"], "error_analisis",
                  "sin contenido textual: prueba 'boveda ocr' para leer el texto en pantalla")
        return
    try:
        respuesta = _crear_mensaje(cli, cfg, PROMPT.read_text(encoding="utf-8"),
                                   _contexto(con, item), ESQUEMA)
        datos = json.loads(_texto_respuesta(respuesta))
        guardar(con, item["id"], cfg.modelo, datos)
        db.marcar(con, item["id"], "analizado", None)
        db.reindexar(con, item["id"])
    except Exception as exc:  # noqa: BLE001
        db.marcar(con, item["id"], "error_analisis", str(exc))


def guardar(con: sqlite3.Connection, item_id: int, modelo: str, datos: dict[str, Any]) -> None:
    hook = datos.get("hook") or {}
    vigencia = datos.get("vigencia") or {}
    con.execute(
        """
        INSERT INTO analisis (item_id, modelo, tipo_contenido, nicho, tema_principal,
                              hook_texto, hook_tecnica, vigencia_estado, valor_historico,
                              analisis_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            modelo = excluded.modelo, tipo_contenido = excluded.tipo_contenido,
            nicho = excluded.nicho, tema_principal = excluded.tema_principal,
            hook_texto = excluded.hook_texto, hook_tecnica = excluded.hook_tecnica,
            vigencia_estado = excluded.vigencia_estado,
            valor_historico = excluded.valor_historico,
            analisis_json = excluded.analisis_json, creado_en = datetime('now')
        """,
        (
            item_id, modelo, datos.get("tipo_contenido"), datos.get("nicho"),
            datos.get("tema_principal"), hook.get("texto"), hook.get("tecnica"),
            vigencia.get("estado"), vigencia.get("valor_historico"),
            json.dumps(datos, ensure_ascii=False),
        ),
    )
    db.etiquetar(con, item_id, datos.get("etiquetas") or [])
    con.commit()
