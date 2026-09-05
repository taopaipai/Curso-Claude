"""Alineacion palabra por palabra con whisperx (alineacion forzada).

Como ya sabemos exactamente que se dice en cada escena —lo acabamos de
sintetizar—, no hace falta transcribir: se le da a whisperx el texto conocido y
el audio, y devuelve en que milisegundo empieza y acaba cada palabra. Eso es
alineacion forzada, y es mucho mas fiable que transcribir y esperar que coincida.

whisperx arrastra torch, asi que es una dependencia opcional:
    pip install "boveda[karaoke]"
Si no esta, el montaje reparte los subtitulos por longitud de texto como antes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# Modelos de alineacion cargados, por idioma: pesan cientos de MB y tardan en
# cargar, asi que se reutilizan durante toda la ejecucion.
_modelos: dict[str, Any] = {}


class ErrorAlineacion(RuntimeError):
    pass


def disponible() -> bool:
    if "whisperx" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("whisperx") is not None
    except (ImportError, ValueError):
        # find_spec revienta si el paquete padre falta o el modulo no tiene spec.
        return False


def _dispositivo(preferido: str) -> str:
    if preferido != "auto":
        return preferido
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001 - sin torch usable, CPU y a correr
        return "cpu"


def _modelo(idioma: str, dispositivo: str):
    clave = f"{idioma}:{dispositivo}"
    if clave not in _modelos:
        import whisperx
        _modelos[clave] = whisperx.load_align_model(
            language_code=idioma, device=dispositivo)
    return _modelos[clave]


def rellenar_huecos(palabras: list[dict[str, Any]], inicio: float,
                    fin: float) -> list[dict[str, Any]]:
    """Da tiempos a las palabras que el alineador no supo colocar.

    Pasa con cifras, siglas y onomatopeyas: whisperx devuelve la palabra sin
    `start`/`end`. En vez de tirar la linea entera, se interpola entre la
    palabra anterior y la siguiente que si tengan tiempo.
    """
    if not palabras:
        return []

    conocidos = [i for i, p in enumerate(palabras)
                 if p.get("inicio") is not None and p.get("fin") is not None]
    if not conocidos:
        # Ninguna palabra tiene tiempo: reparto uniforme sobre la escena.
        paso = (fin - inicio) / len(palabras)
        return [{**p, "inicio": inicio + i * paso, "fin": inicio + (i + 1) * paso}
                for i, p in enumerate(palabras)]

    # Los extremos se anclan al principio y al final de la escena.
    for indice in range(conocidos[0]):
        palabras[indice]["inicio"] = inicio
        palabras[indice]["fin"] = palabras[conocidos[0]]["inicio"]
    for indice in range(conocidos[-1] + 1, len(palabras)):
        palabras[indice]["inicio"] = palabras[conocidos[-1]]["fin"]
        palabras[indice]["fin"] = fin

    for anterior, siguiente in zip(conocidos, conocidos[1:]):
        hueco = list(range(anterior + 1, siguiente))
        if not hueco:
            continue
        desde = palabras[anterior]["fin"]
        hasta = palabras[siguiente]["inicio"]
        # Las palabras sin tiempo se reparten el hueco entero, sin dejar aire.
        paso = (hasta - desde) / len(hueco)
        for posicion, indice in enumerate(hueco):
            palabras[indice]["inicio"] = desde + posicion * paso
            palabras[indice]["fin"] = desde + (posicion + 1) * paso

    # Nunca dejar una palabra con duracion negativa o nula.
    for palabra in palabras:
        if palabra["fin"] <= palabra["inicio"]:
            palabra["fin"] = palabra["inicio"] + 0.05
    return palabras


def alinear(audio: Path, texto: str, duracion: float, *, idioma: str = "es",
            dispositivo: str = "auto") -> list[dict[str, Any]]:
    """Devuelve [{palabra, inicio, fin}] con tiempos relativos al audio dado."""
    if not texto.strip():
        return []
    if not disponible():
        raise ErrorAlineacion(
            'whisperx no esta instalado: pip install "boveda[karaoke]" '
            "o desactiva el karaoke con BOVEDA_KARAOKE=no"
        )

    import whisperx

    device = _dispositivo(dispositivo)
    modelo, metadatos = _modelo(idioma, device)
    onda = whisperx.load_audio(str(audio))

    try:
        resultado = whisperx.align(
            [{"text": texto.strip(), "start": 0.0, "end": duracion}],
            modelo, metadatos, onda, device, return_char_alignments=False,
        )
    except Exception as exc:  # noqa: BLE001 - el alineador puede rendirse
        raise ErrorAlineacion(f"whisperx no pudo alinear la escena: {exc}") from exc

    crudas = resultado.get("word_segments")
    if crudas is None:
        crudas = [p for s in resultado.get("segments", []) for p in s.get("words", [])]

    palabras = [
        {"palabra": (p.get("word") or "").strip(),
         "inicio": p.get("start"), "fin": p.get("end")}
        for p in crudas
        if (p.get("word") or "").strip()
    ]
    return rellenar_huecos(palabras, 0.0, duracion)
