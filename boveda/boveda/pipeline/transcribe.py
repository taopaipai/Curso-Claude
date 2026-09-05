"""Transcribe el audio guardado.

Dos motores:
  local -> faster-whisper corriendo en tu maquina (sin coste por minuto)
  cmd   -> cualquier binario externo; BOVEDA_TRANSCRIBE_CMD con {audio}

Se guardan los segmentos con timestamps porque el analisis posterior necesita
saber en que segundo ocurre el gancho y donde cambia de seccion el video.
"""

from __future__ import annotations

import json
import shlex
import sqlite3
import subprocess
from pathlib import Path

from ..config import Config
from .. import db

_modelo_cache: dict[str, object] = {}


def _transcribir_local(cfg: Config, audio: Path) -> tuple[str, str | None, list[dict]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError(
            'faster-whisper no esta instalado. Usa: pip install "boveda[whisper]" '
            "o cambia BOVEDA_TRANSCRIBE_ENGINE=cmd"
        ) from exc

    clave = f"{cfg.whisper_modelo}:{cfg.whisper_device}"
    if clave not in _modelo_cache:
        device = cfg.whisper_device
        if device == "auto":
            device, tipo = "cpu", "int8"
        else:
            tipo = "float16" if device == "cuda" else "int8"
        _modelo_cache[clave] = WhisperModel(cfg.whisper_modelo, device=device, compute_type=tipo)

    modelo = _modelo_cache[clave]
    segmentos, info = modelo.transcribe(str(audio), vad_filter=True)  # type: ignore[attr-defined]
    lista = [
        {"inicio": round(s.start, 2), "fin": round(s.end, 2), "texto": s.text.strip()}
        for s in segmentos
    ]
    texto = " ".join(s["texto"] for s in lista).strip()
    return texto, getattr(info, "language", None), lista


def _transcribir_cmd(cfg: Config, audio: Path) -> tuple[str, str | None, list[dict]]:
    plantilla = cfg.comando_transcripcion
    if not plantilla:
        raise RuntimeError("BOVEDA_TRANSCRIBE_ENGINE=cmd pero BOVEDA_TRANSCRIBE_CMD esta vacio")
    comando = [p.replace("{audio}", str(audio)) for p in shlex.split(plantilla)]
    salida = subprocess.run(comando, capture_output=True, text=True, check=True)
    return salida.stdout.strip(), None, []


def transcribir(cfg: Config, audio: Path) -> tuple[str, str | None, list[dict]]:
    if cfg.motor_transcripcion == "cmd":
        return _transcribir_cmd(cfg, audio)
    return _transcribir_local(cfg, audio)


def procesar(cfg: Config, con: sqlite3.Connection, item: sqlite3.Row) -> None:
    ruta = item["ruta_audio"]
    if not ruta or not Path(ruta).is_file():
        db.marcar(con, item["id"], "error_transcripcion", "no hay audio descargado")
        return
    try:
        texto, idioma, segmentos = transcribir(cfg, Path(ruta))
        motor = ("faster-whisper:" + cfg.whisper_modelo
                 if cfg.motor_transcripcion == "local" else "cmd")
        con.execute(
            """
            INSERT INTO transcripciones (item_id, motor, idioma, texto, segmentos_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                motor = excluded.motor, idioma = excluded.idioma,
                texto = excluded.texto, segmentos_json = excluded.segmentos_json
            """,
            (item["id"], motor, idioma, texto,
             json.dumps(segmentos, ensure_ascii=False) if segmentos else None),
        )
        if idioma:
            con.execute("UPDATE items SET idioma = COALESCE(idioma, ?) WHERE id = ?",
                        (idioma, item["id"]))
        # Sin voz no es un fallo: es un video de puro texto. Se marca como
        # transcrito y el aviso le dice al siguiente paso que toca OCR.
        aviso = None if texto else "sin voz: el contenido esta en pantalla, ejecuta 'boveda ocr'"
        db.marcar(con, item["id"], "transcrito", aviso)
        db.reindexar(con, item["id"])
    except Exception as exc:  # noqa: BLE001
        db.marcar(con, item["id"], "error_transcripcion", str(exc))
