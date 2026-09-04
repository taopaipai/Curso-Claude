"""Configuracion leida del entorno (y de un .env si existe)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MODELO_POR_DEFECTO = "claude-opus-5"


def _cargar_dotenv(ruta: Path) -> None:
    """Carga un .env sencillo sin dependencias externas. No pisa lo ya exportado."""
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip())


@dataclass(frozen=True)
class Config:
    home: Path
    modelo: str
    motor_transcripcion: str
    whisper_modelo: str
    whisper_device: str
    comando_transcripcion: str | None
    cookies: Path | None
    guardar_video: bool

    @property
    def db(self) -> Path:
        return self.home / "boveda.db"

    @property
    def media(self) -> Path:
        return self.home / "media"

    @property
    def audio(self) -> Path:
        return self.home / "audio"

    @property
    def exports(self) -> Path:
        return self.home / "exports"

    def preparar_directorios(self) -> None:
        for d in (self.home, self.media, self.audio, self.exports):
            d.mkdir(parents=True, exist_ok=True)


def cargar(home: str | os.PathLike[str] | None = None) -> Config:
    _cargar_dotenv(Path.cwd() / ".env")
    raiz = Path(home or os.environ.get("BOVEDA_HOME", "./data")).expanduser().resolve()
    cookies = os.environ.get("BOVEDA_COOKIES_FILE")
    return Config(
        home=raiz,
        modelo=os.environ.get("BOVEDA_MODEL", MODELO_POR_DEFECTO),
        motor_transcripcion=os.environ.get("BOVEDA_TRANSCRIBE_ENGINE", "local"),
        whisper_modelo=os.environ.get("BOVEDA_WHISPER_MODEL", "medium"),
        whisper_device=os.environ.get("BOVEDA_WHISPER_DEVICE", "auto"),
        comando_transcripcion=os.environ.get("BOVEDA_TRANSCRIBE_CMD"),
        cookies=Path(cookies).expanduser() if cookies else None,
        guardar_video=os.environ.get("BOVEDA_KEEP_VIDEO", "0") == "1",
    )
