"""Pruebas del montaje de video. Las que tocan ffmpeg se saltan si no esta."""

import dataclasses
import json
import shutil
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import config, db, montaje, publicador
from tests.test_flujo import ClienteFalso

sin_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requiere ffmpeg")

ESCENAS = [
    {"voz": "Tu bandeja de entrada no es una lista de tareas.",
     "rotulo": "No es una lista", "nota_visual": "primer plano"},
    {"voz": "Regla de dos minutos: si se resuelve rapido, se hace ahora.",
     "rotulo": "Regla de 2 minutos", "nota_visual": "reloj"},
]


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("BOVEDA_TTS_ENGINE", "ninguna")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    db.insertar_item(con, {"plataforma": "tiktok",
                           "url_canonica": "https://www.tiktok.com/@a/video/1"})
    con.execute(
        "INSERT INTO producciones (item_id, formato, titulo, cuerpo, modelo) "
        "VALUES (1, 'reel', 'Tres errores', '0-3s: gancho...\\n3-10s: desarrollo', 'test')"
    )
    con.commit()
    return cfg, con


# --- desglose ----------------------------------------------------------------

def test_desglosar_pide_el_esquema_y_devuelve_escenas(entorno):
    cfg, con = entorno
    cli = ClienteFalso(json.dumps({"escenas": ESCENAS}))
    escenas = montaje.desglosar(cfg, con, 1, cli)

    assert [e["rotulo"] for e in escenas] == ["No es una lista", "Regla de 2 minutos"]
    esquema = cli.ultimo["output_config"]["format"]["schema"]
    assert esquema["properties"]["escenas"]["items"]["required"] == ["voz", "rotulo", "nota_visual"]
    assert "guion" in cli.ultimo["messages"][0]["content"].lower()


def test_desglose_vacio_es_un_error(entorno):
    cfg, con = entorno
    with pytest.raises(montaje.ErrorMontaje, match="ninguna escena"):
        montaje.desglosar(cfg, con, 1, ClienteFalso(json.dumps({"escenas": []})))


# --- subtitulos --------------------------------------------------------------

def test_trocear_subtitulo_no_parte_palabras():
    trozos = montaje.trocear_subtitulo(
        "Tu bandeja de entrada no es una lista de tareas y por eso vives mal", 38)
    assert all(len(t) <= 38 for t in trozos)
    assert " ".join(trozos) == ("Tu bandeja de entrada no es una lista de tareas "
                                "y por eso vives mal")


def test_ass_encadena_tiempos_con_pausa_entre_escenas(tmp_path):
    cfg = config.cargar(tmp_path)
    escenas = [dict(e) for e in ESCENAS]
    escenas[0]["duracion"] = 4.0
    escenas[1]["duracion"] = 5.0
    destino = tmp_path / "s.ass"
    montaje.construir_ass(escenas, destino, cfg)
    texto = destino.read_text(encoding="utf-8")

    assert "Style: Voz," in texto and "Style: Rotulo," in texto
    assert "WrapStyle: 0" in texto          # sin esto el texto se sale del cuadro
    rotulos = [l for l in texto.splitlines() if ",Rotulo," in l]
    assert rotulos[0].startswith("Dialogue: 0,0:00:00.00,0:00:04.00")
    # la segunda escena arranca tras la pausa
    inicio_segunda = 4.0 + montaje.PAUSA_ENTRE_ESCENAS
    assert f"0:00:0{inicio_segunda:.2f}" in rotulos[1]
    voces = [l for l in texto.splitlines() if ",Voz," in l]
    assert len(voces) >= len(escenas)       # cada escena se parte en varias lineas


def test_ass_escapa_las_llaves_que_romperian_el_formato(tmp_path):
    cfg = config.cargar(tmp_path)
    destino = tmp_path / "s.ass"
    montaje.construir_ass(
        [{"voz": "texto con {llaves} y\nsalto", "rotulo": "{raro}", "duracion": 2.0}],
        destino, cfg)
    texto = destino.read_text(encoding="utf-8")
    assert "{llaves}" not in texto and "(llaves)" in texto
    assert "\\N" in texto or "salto" in texto


def test_escena_sin_rotulo_no_genera_linea(tmp_path):
    cfg = config.cargar(tmp_path)
    destino = tmp_path / "s.ass"
    montaje.construir_ass([{"voz": "solo voz", "rotulo": "", "duracion": 2.0}], destino, cfg)
    assert ",Rotulo," not in destino.read_text(encoding="utf-8")


# --- voz ---------------------------------------------------------------------

@sin_ffmpeg
def test_voz_ninguna_genera_silencio_del_largo_estimado(entorno, tmp_path):
    cfg, _ = entorno
    salida = tmp_path / "v.wav"
    duracion = montaje.sintetizar(cfg, "una frase de unos cuarenta caracteres", salida)
    assert salida.is_file()
    assert duracion == pytest.approx(montaje.duracion_estimada(
        "una frase de unos cuarenta caracteres"), abs=0.1)


@sin_ffmpeg
def test_motor_cmd_usa_tu_comando(entorno, tmp_path):
    cfg, _ = entorno
    guion = tmp_path / "tts.py"
    guion.write_text(
        "import sys, wave\n"
        "w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2)\n"
        "w.setframerate(22050); w.writeframes(b'\\x00' * 44100); w.close()\n",
        encoding="utf-8")
    cfg = dataclasses.replace(
        cfg, motor_voz="cmd", comando_voz=f"{sys.executable} {guion} {{salida}}")

    salida = tmp_path / "v.wav"
    duracion = montaje.sintetizar(cfg, "hola", salida)
    assert duracion == pytest.approx(1.0, abs=0.05)


def test_piper_sin_modelo_avisa(entorno, monkeypatch, tmp_path):
    cfg, _ = entorno
    cfg = dataclasses.replace(cfg, motor_voz="piper", voz="")
    monkeypatch.setattr(montaje.shutil, "which", lambda b: "/usr/bin/piper")
    with pytest.raises(montaje.ErrorMontaje, match="BOVEDA_TTS_VOICE"):
        montaje.sintetizar(cfg, "hola", tmp_path / "v.wav")


def test_motor_desconocido_falla_claro(entorno, tmp_path):
    cfg, _ = entorno
    cfg = dataclasses.replace(cfg, motor_voz="magia")
    with pytest.raises(montaje.ErrorMontaje, match="motor de voz desconocido"):
        montaje.sintetizar(cfg, "hola", tmp_path / "v.wav")


# --- montaje completo --------------------------------------------------------

@sin_ffmpeg
def test_montar_produce_un_mp4_y_lo_registra(entorno):
    cfg, con = entorno
    resumen = montaje.montar(cfg, con, 1, escenas=[dict(e) for e in ESCENAS])

    video = resumen["video"]
    assert video.is_file() and video.stat().st_size > 10_000
    assert resumen["duracion"] > 2
    assert resumen["subtitulos"].is_file()

    fila = con.execute("SELECT * FROM montajes WHERE produccion_id = 1").fetchone()
    assert fila["ruta_video"] == str(video)
    assert len(json.loads(fila["escenas_json"])) == 2
    assert fila["duracion_seg"] == pytest.approx(resumen["duracion"], abs=0.01)


@sin_ffmpeg
def test_remontar_reutiliza_el_desglose_sin_volver_a_llamar_al_modelo(entorno):
    cfg, con = entorno
    montaje.montar(cfg, con, 1, escenas=[dict(e) for e in ESCENAS])

    class Prohibido:
        def __getattr__(self, nombre):
            raise AssertionError("no deberia llamarse al modelo")

    resumen = montaje.montar(cfg, con, 1, cli=Prohibido())
    assert resumen["video"].is_file()
    assert [e["rotulo"] for e in resumen["escenas"]] == [e["rotulo"] for e in ESCENAS]


@sin_ffmpeg
def test_montar_con_fondo_de_imagen_recorta_a_vertical(entorno, tmp_path):
    import subprocess
    cfg, con = entorno
    fondo = tmp_path / "fondo.png"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s=1920x1080", "-frames:v", "1",
                    str(fondo)], check=True, capture_output=True)

    resumen = montaje.montar(cfg, con, 1, escenas=[dict(ESCENAS[0])], fondo=fondo)
    salida = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(resumen["video"])],
                            capture_output=True, text=True)
    assert "1080x1920" in salida.stderr


# --- integracion con la publicacion -----------------------------------------

def test_el_publicador_usa_el_video_montado_si_no_le_pasas_media(entorno):
    cfg, con = entorno
    video = cfg.montajes / "video.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4 falso")
    con.execute(
        "INSERT INTO montajes (produccion_id, ruta_video, escenas_json) VALUES (1, ?, '[]')",
        (str(video),),
    )
    publicador.aprobar(con, 1)
    pub_id = publicador.programar(con, 1, "tiktok")
    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()

    pub = publicador._armar(cfg, con, fila)
    assert pub.media == video


def test_sin_montaje_no_se_inventa_un_medio(entorno):
    cfg, con = entorno
    publicador.aprobar(con, 1)
    pub_id = publicador.programar(con, 1, "tiktok")
    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()
    assert publicador._armar(cfg, con, fila).media is None
