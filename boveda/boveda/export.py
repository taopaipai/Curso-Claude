"""Exporta la boveda a archivos legibles (Markdown para Obsidian/Notion, o JSON)."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

RE_NOMBRE = re.compile(r"[^\w\- ]+")


def _nombre(item: sqlite3.Row) -> str:
    base = f"{item['id']:05d} - {(item['titulo'] or item['url_canonica'])[:60]}"
    return RE_NOMBRE.sub("", base).strip() + ".md"


def _lista(valores) -> str:
    return "\n".join(f"- {v}" for v in (valores or [])) or "- (nada)"


def a_markdown(con: sqlite3.Connection, destino: Path) -> int:
    destino.mkdir(parents=True, exist_ok=True)
    filas = con.execute(
        """
        SELECT i.*, a.analisis_json, t.texto AS transcripcion
        FROM items i
        LEFT JOIN analisis a        ON a.item_id = i.id
        LEFT JOIN transcripciones t ON t.item_id = i.id
        ORDER BY i.id
        """
    ).fetchall()

    for item in filas:
        analisis = json.loads(item["analisis_json"]) if item["analisis_json"] else {}
        hook = analisis.get("hook") or {}
        vigencia = analisis.get("vigencia") or {}
        aplic = analisis.get("aplicabilidad") or {}
        etiquetas = analisis.get("etiquetas") or []

        producciones = con.execute(
            "SELECT formato, nicho, cuerpo FROM producciones WHERE item_id = ? ORDER BY id",
            (item["id"],),
        ).fetchall()

        partes = [
            "---",
            f"id: {item['id']}",
            f"plataforma: {item['plataforma']}",
            f"url: {item['url_canonica']}",
            f"autor: {item['autor'] or ''}",
            f"publicado: {item['publicado_en'] or ''}",
            f"guardado: {item['guardado_en'] or ''}",
            f"tipo: {analisis.get('tipo_contenido', '')}",
            f"nicho: {analisis.get('nicho', '')}",
            f"vigencia: {vigencia.get('estado', '')}",
            f"valor_historico: {vigencia.get('valor_historico', '')}",
            "tags: [" + ", ".join(etiquetas) + "]",
            "---",
            "",
            f"# {item['titulo'] or item['url_canonica']}",
            "",
            "## Gancho",
            f"> {hook.get('texto', '(sin analizar)')}",
            "",
            f"**Tecnica:** {hook.get('tecnica', '-')} · **Segundo:** {hook.get('segundos', '-')}",
            "",
            hook.get("por_que_funciona", ""),
            "",
            "## Estructura",
        ]
        for bloque in analisis.get("estructura") or []:
            partes.append(
                f"- **{bloque.get('inicio_seg', '?')}-{bloque.get('fin_seg', '?')}s "
                f"· {bloque.get('seccion', '')}** — {bloque.get('proposito', '')}"
            )
        partes += [
            "",
            "## Por que funciona",
            _lista((analisis.get("por_que_funciona") or {}).get("factores")),
            "",
            "## Aplicable para nosotros",
            _lista(aplic.get("para_nosotros")),
            "",
            "## Para ensenar",
            _lista(aplic.get("para_ensenar")),
            "",
            "## Vigencia",
            vigencia.get("razon", "(sin analizar)"),
            "",
        ]
        for prod in producciones:
            partes += [f"## Produccion — {prod['formato']} ({prod['nicho'] or 'nicho original'})",
                       prod["cuerpo"], ""]
        if item["transcripcion"]:
            partes += ["## Transcripcion", "", item["transcripcion"], ""]

        (destino / _nombre(item)).write_text("\n".join(partes), encoding="utf-8")
    return len(filas)


def a_json(con: sqlite3.Connection, destino: Path) -> int:
    filas = con.execute(
        """
        SELECT i.*, a.analisis_json, t.texto AS transcripcion
        FROM items i
        LEFT JOIN analisis a        ON a.item_id = i.id
        LEFT JOIN transcripciones t ON t.item_id = i.id
        ORDER BY i.id
        """
    ).fetchall()
    datos = []
    for item in filas:
        registro = dict(item)
        registro["analisis"] = json.loads(item["analisis_json"]) if item["analisis_json"] else None
        registro.pop("analisis_json", None)
        registro["crudo_json"] = None  # ya esta en la base; aqui solo estorba
        datos.append(registro)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(datos)
