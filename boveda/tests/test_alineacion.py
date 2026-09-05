"""Pruebas de la alineacion palabra por palabra. whisperx va simulado."""

import json
import shutil
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import alineacion, config, db, montaje

sin_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requiere ffmpeg")


def _whisperx_falso(reparto=None):
    """Devuelve un modulo whisperx que reparte las palabras por el audio."""
    modulo = types.ModuleType("whisperx")
    modulo.llamadas = []
    modulo.load_align_model = lambda language_code, device: ("modelo", {"lang": language_code})
    modulo.load_audio = lambda ruta: [0.0]

    def align(segmentos, modelo, metadatos, audio, device, return_char_alignments=False):
        modulo.llamadas.append((segmentos[0]["text"], segmentos[0]["end"], device))
        if reparto is not None:
            return reparto
        palabras = segmentos[0]["text"].split()
        paso = segmentos[0]["end"] / len(palabras)
        return {"word_segments": [
            {"word": p, "start": i * paso, "end": (i + 1) * paso}
            for i, p in enumerate(palabras)
        ]}

    modulo.align = align
    return modulo


@pytest.fixture()
def whisperx(monkeypatch):
    modulo = _whisperx_falso()
    monkeypatch.setitem(sys.modules, "whisperx", modulo)
    monkeypatch.setattr(alineacion, "_modelos", {})
    return modulo


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("BOVEDA_TTS_ENGINE", "ninguna")
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    db.insertar_item(con, {"plataforma": "tiktok", "url_canonica": "https://t/1"})
    con.execute("INSERT INTO producciones (item_id, formato, titulo, cuerpo, modelo) "
                "VALUES (1, 'reel', 'T', 'guion', 'test')")
    con.commit()
    return cfg, con


# --- huecos ------------------------------------------------------------------

def _p(nombre, inicio=None, fin=None):
    return {"palabra": nombre, "inicio": inicio, "fin": fin}


def test_rellena_los_extremos_sin_tiempo():
    palabras = alineacion.rellenar_huecos(
        [_p("a"), _p("b", 1.0, 1.5), _p("c")], 0.0, 3.0)
    assert palabras[0]["inicio"] == 0.0 and palabras[0]["fin"] == 1.0
    assert palabras[2]["inicio"] == 1.5 and palabras[2]["fin"] == 3.0


def test_reparte_los_huecos_del_medio_sin_dejar_aire():
    palabras = alineacion.rellenar_huecos(
        [_p("a", 0.0, 1.0), _p("b"), _p("c"), _p("d", 4.0, 5.0)], 0.0, 5.0)
    assert palabras[1]["inicio"] == 1.0
    assert palabras[1]["fin"] == palabras[2]["inicio"] == 2.5
    assert palabras[2]["fin"] == 4.0          # engancha con la siguiente conocida


def test_sin_ningun_tiempo_reparte_uniforme():
    palabras = alineacion.rellenar_huecos([_p("a"), _p("b"), _p("c")], 0.0, 3.0)
    assert [p["inicio"] for p in palabras] == [0.0, 1.0, 2.0]
    assert palabras[-1]["fin"] == 3.0


def test_ninguna_palabra_queda_con_duracion_cero():
    palabras = alineacion.rellenar_huecos(
        [_p("a", 1.0, 1.0), _p("b", 1.0, 0.5)], 0.0, 2.0)
    assert all(p["fin"] > p["inicio"] for p in palabras)


# --- alinear -----------------------------------------------------------------

def test_alinear_devuelve_una_palabra_por_token(whisperx, tmp_path):
    audio = tmp_path / "v.wav"
    audio.write_bytes(b"")
    palabras = alineacion.alinear(audio, "hola mundo cruel", 3.0, idioma="es")

    assert [p["palabra"] for p in palabras] == ["hola", "mundo", "cruel"]
    assert palabras[0]["inicio"] == 0.0 and palabras[-1]["fin"] == pytest.approx(3.0)
    texto, duracion, _ = whisperx.llamadas[0]
    assert (texto, duracion) == ("hola mundo cruel", 3.0)   # alineacion forzada


def test_alinear_acepta_la_forma_por_segmentos(monkeypatch, tmp_path):
    modulo = _whisperx_falso(reparto={"segments": [
        {"words": [{"word": "uno", "start": 0.0, "end": 0.5},
                   {"word": "  ", "start": 0.5, "end": 0.6},
                   {"word": "dos", "start": 0.6, "end": 1.0}]}]})
    monkeypatch.setitem(sys.modules, "whisperx", modulo)
    monkeypatch.setattr(alineacion, "_modelos", {})

    audio = tmp_path / "v.wav"
    audio.write_bytes(b"")
    palabras = alineacion.alinear(audio, "uno dos", 1.0)
    assert [p["palabra"] for p in palabras] == ["uno", "dos"]   # se cae lo vacio


def test_alinear_interpola_lo_que_whisperx_no_supo_colocar(monkeypatch, tmp_path):
    modulo = _whisperx_falso(reparto={"word_segments": [
        {"word": "son", "start": 0.0, "end": 0.4},
        {"word": "1998", "start": None, "end": None},
        {"word": "euros", "start": 1.2, "end": 1.6}]})
    monkeypatch.setitem(sys.modules, "whisperx", modulo)
    monkeypatch.setattr(alineacion, "_modelos", {})

    audio = tmp_path / "v.wav"
    audio.write_bytes(b"")
    palabras = alineacion.alinear(audio, "son 1998 euros", 2.0)
    assert palabras[1]["inicio"] == 0.4 and palabras[1]["fin"] == 1.2


def test_alinear_sin_whisperx_avisa_de_como_instalarlo(monkeypatch, tmp_path):
    monkeypatch.setattr(alineacion, "disponible", lambda: False)
    with pytest.raises(alineacion.ErrorAlineacion, match="boveda\\[karaoke\\]"):
        alineacion.alinear(tmp_path / "v.wav", "hola", 1.0)


def test_si_el_alineador_revienta_se_envuelve_el_error(monkeypatch, tmp_path):
    modulo = _whisperx_falso()
    modulo.align = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin memoria"))
    monkeypatch.setitem(sys.modules, "whisperx", modulo)
    monkeypatch.setattr(alineacion, "_modelos", {})

    audio = tmp_path / "v.wav"
    audio.write_bytes(b"")
    with pytest.raises(alineacion.ErrorAlineacion, match="sin memoria"):
        alineacion.alinear(audio, "hola", 1.0)


def test_texto_vacio_no_llama_al_alineador(whisperx, tmp_path):
    assert alineacion.alinear(tmp_path / "v.wav", "   ", 1.0) == []
    assert whisperx.llamadas == []


def test_el_modelo_se_carga_una_sola_vez_por_idioma(whisperx, tmp_path, monkeypatch):
    cargas = []
    whisperx.load_align_model = lambda language_code, device: (
        cargas.append(language_code) or ("m", {}))
    audio = tmp_path / "v.wav"
    audio.write_bytes(b"")
    alineacion.alinear(audio, "uno dos", 1.0, idioma="es")
    alineacion.alinear(audio, "tres", 1.0, idioma="es")
    assert cargas == ["es"]


# --- subtitulos de karaoke ---------------------------------------------------

def test_agrupar_palabras_respeta_el_ancho_de_linea():
    palabras = [_p(f"palabra{i}", i, i + 0.5) for i in range(10)]
    grupos = montaje.agrupar_palabras(palabras, limite=20)
    for grupo in grupos:
        assert len(" ".join(p["palabra"] for p in grupo)) <= 20
    assert sum(len(g) for g in grupos) == 10


def test_el_ass_lleva_etiquetas_k_cuando_hay_palabras(tmp_path):
    cfg = config.cargar(tmp_path)
    escena = {"voz": "hola mundo", "rotulo": "", "duracion": 2.0, "palabras": [
        _p("hola", 0.0, 0.8), _p("mundo", 1.0, 2.0)]}
    destino = tmp_path / "s.ass"
    montaje.construir_ass([escena], destino, cfg)
    texto = destino.read_text(encoding="utf-8")

    assert "Style: Karaoke," in texto
    linea = next(l for l in texto.splitlines() if ",Karaoke," in l)
    # la primera palabra se ilumina hasta que empieza la segunda: 1.0 s
    assert "{\\k100}hola" in linea
    assert "{\\k100}mundo" in linea
    assert "SecondaryColour" in texto      # sin esto el karaoke no tendria dos colores


def test_sin_palabras_se_mantienen_los_subtitulos_por_bloques(tmp_path):
    cfg = config.cargar(tmp_path)
    destino = tmp_path / "s.ass"
    montaje.construir_ass(
        [{"voz": "hola mundo", "rotulo": "", "duracion": 2.0, "palabras": []}],
        destino, cfg)
    texto = destino.read_text(encoding="utf-8")
    assert ",Voz," in texto and ",Karaoke," not in texto
    assert "\\k" not in texto


def test_las_palabras_se_desplazan_con_la_escena(tmp_path):
    cfg = config.cargar(tmp_path)
    escenas = [
        {"voz": "uno", "rotulo": "", "duracion": 1.0, "palabras": [_p("uno", 0.0, 1.0)]},
        {"voz": "dos", "rotulo": "", "duracion": 1.0, "palabras": [_p("dos", 0.0, 1.0)]},
    ]
    destino = tmp_path / "s.ass"
    montaje.construir_ass(escenas, destino, cfg)
    lineas = [l for l in destino.read_text(encoding="utf-8").splitlines() if ",Karaoke," in l]
    inicio_segunda = 1.0 + montaje.PAUSA_ENTRE_ESCENAS
    assert lineas[1].split(",")[1] == montaje._tiempo_ass(inicio_segunda)


# --- integracion con el montaje ---------------------------------------------

def test_quiere_karaoke_segun_la_configuracion(entorno, monkeypatch):
    import dataclasses
    cfg, _ = entorno
    monkeypatch.setattr(alineacion, "disponible", lambda: False)

    assert montaje.quiere_karaoke(dataclasses.replace(cfg, karaoke="no")) is False
    assert montaje.quiere_karaoke(dataclasses.replace(cfg, karaoke="auto")) is False
    with pytest.raises(montaje.ErrorMontaje, match="whisperx no esta instalado"):
        montaje.quiere_karaoke(dataclasses.replace(cfg, karaoke="si"))

    monkeypatch.setattr(alineacion, "disponible", lambda: True)
    assert montaje.quiere_karaoke(dataclasses.replace(cfg, karaoke="auto")) is True


@sin_ffmpeg
def test_montar_con_karaoke_guarda_las_palabras(entorno, whisperx):
    cfg, con = entorno
    resumen = montaje.montar(cfg, con, 1, escenas=[
        {"voz": "Tu bandeja no es una lista.", "rotulo": "Gancho", "nota_visual": "x"}])

    assert resumen["karaoke"] is True
    assert resumen["avisos"] == []
    palabras = resumen["escenas"][0]["palabras"]
    assert [p["palabra"] for p in palabras][:2] == ["Tu", "bandeja"]
    assert "\\k" in resumen["subtitulos"].read_text(encoding="utf-8")
    assert resumen["video"].is_file()


@sin_ffmpeg
def test_si_falla_la_alineacion_el_video_se_monta_igual(entorno, monkeypatch, whisperx):
    cfg, con = entorno
    monkeypatch.setattr(alineacion, "alinear", lambda *a, **k: (_ for _ in ()).throw(
        alineacion.ErrorAlineacion("modelo corrupto")))

    resumen = montaje.montar(cfg, con, 1, escenas=[
        {"voz": "Una frase cualquiera.", "rotulo": "R", "nota_visual": "x"}])

    assert resumen["karaoke"] is False
    assert "modelo corrupto" in resumen["avisos"][0]
    assert resumen["video"].is_file()
    assert ",Voz," in resumen["subtitulos"].read_text(encoding="utf-8")


@sin_ffmpeg
def test_remontar_no_arrastra_los_tiempos_del_montaje_anterior(entorno, whisperx):
    cfg, con = entorno
    escenas = [{"voz": "Una frase larga que dura lo suyo.", "rotulo": "R", "nota_visual": "x"}]
    primero = montaje.montar(cfg, con, 1, escenas=escenas)
    assert primero["escenas"][0]["palabras"]        # el primer montaje si las tenia

    # se vuelve a montar sin karaoke: las palabras se recalculan, no se heredan
    import dataclasses
    segundo = montaje.montar(dataclasses.replace(cfg, karaoke="no"), con, 1)
    assert segundo["escenas"][0]["palabras"] == []
    assert segundo["escenas"][0]["duracion"] > 0    # la duracion si se recalcula
    assert ",Karaoke," not in segundo["subtitulos"].read_text(encoding="utf-8")

    guardado = json.loads(con.execute(
        "SELECT escenas_json FROM montajes WHERE produccion_id = 1").fetchone()[0])
    assert guardado[0]["palabras"] == []            # y no quedan tiempos viejos guardados
