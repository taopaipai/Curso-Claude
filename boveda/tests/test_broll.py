"""Pruebas del b-roll. No se descarga nada: los bancos van simulados."""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import broll, config, db, montaje, web

sin_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requiere ffmpeg")

RESPUESTA_PEXELS = {"videos": [{
    "id": 555, "url": "https://www.pexels.com/video/555/",
    "user": {"name": "Ana Foto"},
    "video_files": [
        {"link": "https://cdn.pexels.com/pequeno.mp4", "width": 360, "height": 640},
        {"link": "https://cdn.pexels.com/vertical.mp4", "width": 1080, "height": 1920},
    ],
}]}

RESPUESTA_PIXABAY = {"hits": [{
    "id": 777, "pageURL": "https://pixabay.com/videos/777/", "user": "Luis",
    "videos": {"large": {"url": "https://cdn.pixabay.com/grande.mp4"},
               "small": {"url": "https://cdn.pixabay.com/pequeno.mp4"}},
}]}


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("BOVEDA_TTS_ENGINE", "ninguna")
    monkeypatch.setenv("BOVEDA_KARAOKE", "no")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    db.insertar_item(con, {"plataforma": "tiktok", "url_canonica": "https://t/1"})
    con.execute("INSERT INTO producciones (item_id, formato, titulo, cuerpo, modelo) "
                "VALUES (1, 'reel', 'T', 'guion', 'test')")
    con.commit()
    return cfg, con


class Banco:
    """Sustituye a web.pedir/web.descargar y anota lo que se pidio."""

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.consultas: list[str] = []
        self.descargas: list[str] = []

    def pedir(self, url, metodo="GET", **kwargs):
        self.consultas.append(url)
        return self.respuesta

    def descargar(self, url, destino, **kwargs):
        self.descargas.append(url)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"video falso")
        return destino


def _enchufar(monkeypatch, banco):
    monkeypatch.setattr(broll.web, "pedir", banco.pedir)
    monkeypatch.setattr(broll.web, "descargar", banco.descargar)


# --- terminos de busqueda ----------------------------------------------------

def test_terminos_quita_relleno_y_repeticiones():
    assert broll.terminos("primer plano de una persona con un ordenador",
                          "la persona escribe") == "persona ordenador escribe"


def test_terminos_se_queda_en_cuatro_palabras():
    assert len(broll.terminos("uno dos tres cuatro cinco seis").split()) == 4


# --- proveedores -------------------------------------------------------------

def test_pexels_elige_el_archivo_vertical(entorno, monkeypatch):
    cfg, _ = entorno
    monkeypatch.setenv("PEXELS_API_KEY", "clave")
    banco = Banco(RESPUESTA_PEXELS)
    _enchufar(monkeypatch, banco)

    clip = broll._pexels(cfg, "person typing laptop", 4.0)

    assert clip.origen == "pexels" and clip.autor == "Ana Foto"
    assert banco.descargas == ["https://cdn.pexels.com/vertical.mp4"]
    assert "orientation=portrait" in banco.consultas[0]
    assert "person+typing+laptop" in banco.consultas[0]
    assert clip.ruta.is_file()


def test_lo_descargado_se_cachea(entorno, monkeypatch):
    cfg, _ = entorno
    monkeypatch.setenv("PEXELS_API_KEY", "clave")
    banco = Banco(RESPUESTA_PEXELS)
    _enchufar(monkeypatch, banco)

    broll._pexels(cfg, "gatos", 4.0)
    broll._pexels(cfg, "gatos", 4.0)
    assert len(banco.descargas) == 1        # el segundo montaje no vuelve a bajarlo


def test_sin_clave_el_proveedor_se_salta(entorno, monkeypatch):
    cfg, _ = entorno
    _enchufar(monkeypatch, Banco(RESPUESTA_PEXELS))
    assert broll._pexels(cfg, "gatos", 4.0) is None
    assert broll._pixabay(cfg, "gatos", 4.0) is None


def test_pixabay_prefiere_el_formato_grande(entorno, monkeypatch):
    cfg, _ = entorno
    monkeypatch.setenv("PIXABAY_API_KEY", "clave")
    banco = Banco(RESPUESTA_PIXABAY)
    _enchufar(monkeypatch, banco)

    clip = broll._pixabay(cfg, "reloj", 3.0)
    assert banco.descargas == ["https://cdn.pixabay.com/grande.mp4"]
    assert clip.url == "https://pixabay.com/videos/777/"


def test_videoteca_local_gana_el_archivo_con_mas_coincidencias(entorno, monkeypatch, tmp_path):
    import dataclasses
    cfg, _ = entorno
    carpeta = tmp_path / "clips"
    carpeta.mkdir()
    (carpeta / "playa-atardecer.mp4").write_bytes(b"x")
    (carpeta / "oficina-persona-ordenador.mp4").write_bytes(b"x")
    (carpeta / "notas.txt").write_text("no es un video")
    cfg = dataclasses.replace(cfg, broll_propio=str(carpeta))

    clip = broll._local(cfg, "persona ordenador escribiendo", 3.0)
    assert clip.ruta.name == "oficina-persona-ordenador.mp4"
    assert clip.origen == "local"
    assert broll._local(cfg, "montanas nieve", 3.0) is None


def test_sin_carpeta_local_no_falla(entorno):
    cfg, _ = entorno
    assert broll._local(cfg, "lo que sea", 3.0) is None


# --- eleccion ----------------------------------------------------------------

@sin_ffmpeg
def test_buscar_acaba_en_generado_cuando_no_hay_nada(entorno):
    cfg, _ = entorno
    clip = broll.buscar(cfg, "algo muy raro", 2.0)
    assert clip.origen == "generado"
    assert clip.ruta.is_file()


@sin_ffmpeg
def test_un_banco_caido_no_tumba_el_montaje(entorno, monkeypatch):
    cfg, _ = entorno
    monkeypatch.setenv("PEXELS_API_KEY", "clave")
    monkeypatch.setattr(broll.web, "pedir",
                        lambda *a, **k: (_ for _ in ()).throw(web.ErrorHttp("HTTP 429: cuota")))
    avisos: list[str] = []

    clip = broll.buscar(cfg, "gatos", 2.0, avisos)
    assert clip.origen == "generado"
    assert "cuota" in avisos[0] and "pexels" in avisos[0]


@sin_ffmpeg
def test_cada_consulta_genera_un_fondo_distinto(entorno):
    cfg, _ = entorno
    uno = broll._generado(cfg, "person typing laptop", 2.0)
    otro = broll._generado(cfg, "city night traffic", 2.0)
    assert uno.ruta != otro.ruta


def test_los_creditos_solo_citan_lo_que_es_de_otros():
    clips = [
        broll.Clip(Path("a.mp4"), "pexels", "Ana", "https://p/1", "gatos"),
        broll.Clip(Path("b.mp4"), "pexels", "Ana", "https://p/1", "gatos"),
        broll.Clip(Path("c.mp4"), "generado", consulta="x"),
        broll.Clip(Path("d.mp4"), "local", consulta="y"),
    ]
    texto = broll.creditos(clips)
    assert texto.count("https://p/1") == 1      # sin repetir
    assert "generado" not in texto and "local" not in texto
    assert broll.creditos(clips[2:]) == ""


# --- integracion con el montaje ---------------------------------------------

def _duracion(video: Path) -> float:
    """Mide un video decodificandolo a nada: aqui no siempre hay ffprobe."""
    import re
    import subprocess
    salida = subprocess.run(
        [shutil.which("ffmpeg"), "-hide_banner", "-i", str(video), "-f", "null", "-"],
        capture_output=True, text=True).stderr
    horas, minutos, segundos = re.findall(
        r"time=(\d+):(\d+):(\d+\.\d+)", salida)[-1]
    return int(horas) * 3600 + int(minutos) * 60 + float(segundos)


@sin_ffmpeg
def test_construir_broll_hace_un_segmento_por_escena(entorno, tmp_path):
    cfg, _ = entorno
    escenas = [
        {"voz": "uno", "duracion": 2.0, "busqueda_broll": "person typing laptop"},
        {"voz": "dos", "duracion": 3.0, "busqueda_broll": "hourglass desk"},
    ]
    trabajo = tmp_path / "trabajo"
    trabajo.mkdir()
    fondo, clips = montaje.construir_broll(cfg, escenas, trabajo, shutil.which("ffmpeg"))

    assert fondo.is_file() and len(clips) == 2
    assert sorted(f.name for f in trabajo.glob("bg_*.mp4")) == ["bg_000.mp4", "bg_001.mp4"]
    assert escenas[0]["broll"]["consulta"] == "person typing laptop"

    # el fondo cubre las dos escenas MAS la pausa que va entre ellas
    esperado = 2.0 + montaje.PAUSA_ENTRE_ESCENAS + 3.0
    assert _duracion(fondo) == pytest.approx(esperado, abs=0.15)


@sin_ffmpeg
def test_montar_pone_un_clip_por_escena_y_escribe_los_creditos(entorno, monkeypatch):
    cfg, con = entorno
    monkeypatch.setenv("PEXELS_API_KEY", "clave")
    banco = Banco(RESPUESTA_PEXELS)
    _enchufar(monkeypatch, banco)
    # el clip descargado tiene que ser un video de verdad para que ffmpeg lo lea
    import subprocess

    def descargar_real(url, destino, **kwargs):
        destino.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=green:s=640x360:d=2",
                        "-pix_fmt", "yuv420p", str(destino)], check=True, capture_output=True)
        return destino

    monkeypatch.setattr(broll.web, "descargar", descargar_real)

    resumen = montaje.montar(cfg, con, 1, escenas=[
        {"voz": "Primera frase.", "rotulo": "A", "nota_visual": "x",
         "busqueda_broll": "person typing laptop"},
        {"voz": "Segunda frase.", "rotulo": "B", "nota_visual": "x",
         "busqueda_broll": "hourglass desk"},
    ])

    assert [c.origen for c in resumen["clips"]] == ["pexels", "pexels"]
    creditos = resumen["video"].parent / "creditos.txt"
    assert "Ana Foto" in creditos.read_text(encoding="utf-8")
    assert resumen["video"].is_file()


@sin_ffmpeg
def test_sin_broll_se_vuelve_al_fondo_plano(entorno):
    cfg, con = entorno
    resumen = montaje.montar(cfg, con, 1, con_broll=False, escenas=[
        {"voz": "Una frase.", "rotulo": "A", "nota_visual": "x"}])
    assert resumen["clips"] == []
    assert not (resumen["video"].parent / "fondo.mp4").exists()


@sin_ffmpeg
def test_un_fondo_dado_a_mano_manda_sobre_el_broll(entorno, tmp_path):
    import subprocess
    cfg, con = entorno
    fondo = tmp_path / "mio.png"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=blue:s=200x200", "-frames:v", "1",
                    str(fondo)], check=True, capture_output=True)

    resumen = montaje.montar(cfg, con, 1, fondo=fondo, escenas=[
        {"voz": "Una frase.", "rotulo": "A", "nota_visual": "x"}])
    assert resumen["clips"] == []


@sin_ffmpeg
def test_remontar_no_arrastra_el_broll_anterior(entorno):
    cfg, con = entorno
    montaje.montar(cfg, con, 1, escenas=[
        {"voz": "Una frase.", "rotulo": "A", "nota_visual": "escritorio",
         "busqueda_broll": "desk"}])

    segundo = montaje.montar(cfg, con, 1, con_broll=False)
    assert segundo["escenas"][0].get("broll") is None
