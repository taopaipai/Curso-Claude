"""Configuracion leida del entorno (y de un .env si existe)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MODELO_POR_DEFECTO = "claude-opus-5"

# Todo lo del proyecto vive bajo esta carpeta: codigo, datos, media y
# credenciales. Se ancla al paquete, no al directorio desde el que ejecutas,
# para que `boveda` guarde siempre en el mismo sitio lo llames desde donde
# lo llames.
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent


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
    motor_ocr: str
    max_fotogramas: int
    umbral_ocr: int
    idiomas_ocr: str
    url_base_media: str | None
    motor_voz: str
    voz: str
    comando_voz: str | None
    resolucion: str
    fuente: str
    karaoke: str
    idioma_voz: str
    dispositivo_alineacion: str

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
    def fotogramas(self) -> Path:
        return self.home / "fotogramas"

    @property
    def montajes(self) -> Path:
        return self.home / "montajes"

    @property
    def publicaciones(self) -> Path:
        return self.home / "publicaciones"

    @property
    def exports(self) -> Path:
        return self.home / "exports"

    def preparar_directorios(self) -> None:
        for d in (self.home, self.media, self.audio, self.fotogramas,
                  self.montajes, self.publicaciones, self.exports):
            d.mkdir(parents=True, exist_ok=True)


def cargar(home: str | os.PathLike[str] | None = None) -> Config:
    _cargar_dotenv(RAIZ_PROYECTO / ".env")
    _cargar_dotenv(Path.cwd() / ".env")
    raiz = Path(
        home or os.environ.get("BOVEDA_HOME") or RAIZ_PROYECTO / "data"
    ).expanduser().resolve()
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
        motor_ocr=os.environ.get("BOVEDA_OCR_ENGINE", "claude"),
        max_fotogramas=int(os.environ.get("BOVEDA_OCR_MAX_FRAMES", "12")),
        umbral_ocr=int(os.environ.get("BOVEDA_OCR_UMBRAL", "200")),
        idiomas_ocr=os.environ.get("BOVEDA_OCR_LANGS", "spa+eng"),
        url_base_media=(os.environ.get("BOVEDA_MEDIA_BASE_URL") or "").rstrip("/") or None,
        motor_voz=os.environ.get("BOVEDA_TTS_ENGINE", "piper"),
        voz=os.environ.get("BOVEDA_TTS_VOICE", ""),
        comando_voz=os.environ.get("BOVEDA_TTS_CMD"),
        resolucion=os.environ.get("BOVEDA_RESOLUCION", "1080x1920"),
        fuente=os.environ.get("BOVEDA_FUENTE", "DejaVu Sans"),
        karaoke=os.environ.get("BOVEDA_KARAOKE", "auto"),
        idioma_voz=os.environ.get("BOVEDA_IDIOMA_VOZ", "es"),
        dispositivo_alineacion=os.environ.get("BOVEDA_ALIGN_DEVICE", "auto"),
    )
