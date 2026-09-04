"""Pruebas del OCR de fotogramas. Sin red: el cliente de Claude va simulado."""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import config, db, export
from boveda.pipeline import analyze, ocr
from tests.test_flujo import ANALISIS, ClienteFalso

LECTURA = {
    "fotogramas": [
        {"indice": 0, "texto_en_pantalla": "3 errores que te cuestan dinero",
         "descripcion_visual": "texto blanco sobre fondo negro"},
        {"indice": 1, "texto_en_pantalla": "1. No revisas tus suscripciones",
         "descripcion_visual": ""},
        {"indice": 2, "texto_en_pantalla": "1. No revisas tus suscripciones",
         "descripcion_visual": "misma lamina"},
        {"indice": 3, "texto_en_pantalla": "2. Pagas comisiones que no ves",
         "descripcion_visual": ""},
    ],
    "texto_unificado": "3 errores que te cuestan dinero\n1. No revisas tus suscripciones\n"
                       "2. Pagas comisiones que no ves",
    "idioma": "es",
}


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    return cfg, con


def _item_mudo(con, cfg, texto_voz=""):
    """Un reel de puro texto: audio sin voz, video descargado."""
    item_id, _ = db.insertar_item(con, {
        "plataforma": "instagram",
        "url_canonica": "https://www.instagram.com/p/MUDO1/",
        "titulo": "3 errores", "duracion_seg": 12,
    })
    video = cfg.media / f"{item_id}.mp4"
    video.write_bytes(b"video falso")
    con.execute("UPDATE items SET ruta_media = ? WHERE id = ?", (str(video), item_id))
    con.execute(
        "INSERT INTO transcripciones (item_id, motor, idioma, texto) VALUES (?, 'test', 'es', ?)",
        (item_id, texto_voz),
    )
    db.marcar(con, item_id, "transcrito")
    return item_id


def _fotogramas_falsos(cfg, item_id, n=4):
    destino = cfg.fotogramas / str(item_id)
    destino.mkdir(parents=True, exist_ok=True)
    salida = []
    for i in range(n):
        ruta = destino / f"f_{i:03d}.jpg"
        ruta.write_bytes(b"\xff\xd8\xff\xdb jpeg falso")
        salida.append((i, float(i * 3), ruta))
    return salida


def test_unificar_salta_repeticiones_y_texto_que_crece():
    leidos = [
        {"texto_en_pantalla": "Ahorra"},
        {"texto_en_pantalla": "Ahorra"},            # lamina repetida: se ignora
        {"texto_en_pantalla": "Ahorra mas dinero"},  # el rotulo se sigue escribiendo
        {"texto_en_pantalla": ""},
        {"texto_en_pantalla": "Paso 2"},
    ]
    assert ocr.unificar(leidos) == "Ahorra\nmas dinero\nPaso 2"


def test_candidatos_solo_los_mudos_salvo_todos(entorno):
    cfg, con = entorno
    mudo = _item_mudo(con, cfg)
    hablado, _ = db.insertar_item(con, {
        "plataforma": "youtube",
        "url_canonica": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    })
    con.execute(
        "INSERT INTO transcripciones (item_id, motor, texto) VALUES (?, 'test', ?)",
        (hablado, "palabra " * 100),
    )
    db.marcar(con, hablado, "transcrito")

    assert [f["id"] for f in ocr.candidatos(con, umbral=200)] == [mudo]
    assert {f["id"] for f in ocr.candidatos(con, 200, todos=True)} == {mudo, hablado}


def test_ocr_con_claude_guarda_texto_y_fotogramas(entorno, monkeypatch):
    cfg, con = entorno
    item_id = _item_mudo(con, cfg)
    monkeypatch.setattr(ocr, "extraer_fotogramas",
                        lambda *a, **k: _fotogramas_falsos(cfg, item_id))
    cli = ClienteFalso(json.dumps(LECTURA))

    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    ocr.procesar(cfg, con, item, cli)

    fila = con.execute("SELECT * FROM ocr WHERE item_id = ?", (item_id,)).fetchone()
    assert fila["n_fotogramas"] == 4
    assert "comisiones" in fila["texto"]
    assert fila["motor"].startswith("claude:")

    marcos = con.execute(
        "SELECT segundo, texto FROM fotogramas WHERE item_id = ? ORDER BY indice", (item_id,)
    ).fetchall()
    assert [m["segundo"] for m in marcos] == [0.0, 3.0, 6.0, 9.0]
    assert marcos[0]["texto"] == "3 errores que te cuestan dinero"

    # las imagenes viajan como bloques de imagen, no como texto
    bloques = cli.ultimo["messages"][0]["content"]
    assert sum(1 for b in bloques if b["type"] == "image") == 4
    assert bloques[1]["source"]["media_type"] == "image/jpeg"


def test_ocr_alimenta_la_busqueda(entorno, monkeypatch):
    cfg, con = entorno
    item_id = _item_mudo(con, cfg)
    monkeypatch.setattr(ocr, "extraer_fotogramas",
                        lambda *a, **k: _fotogramas_falsos(cfg, item_id))
    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    ocr.procesar(cfg, con, item, ClienteFalso(json.dumps(LECTURA)))

    assert db.buscar(con, "suscripciones")[0]["item_id"] == item_id


def test_analisis_de_pieza_muda_usa_el_texto_en_pantalla(entorno, monkeypatch):
    cfg, con = entorno
    item_id = _item_mudo(con, cfg)
    monkeypatch.setattr(ocr, "extraer_fotogramas",
                        lambda *a, **k: _fotogramas_falsos(cfg, item_id))
    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    ocr.procesar(cfg, con, item, ClienteFalso(json.dumps(LECTURA)))

    cli = ClienteFalso(json.dumps(ANALISIS))
    analyze.procesar(cfg, con, item, cli)

    enviado = cli.ultimo["messages"][0]["content"]
    assert "TEXTO EN PANTALLA" in enviado
    assert "comisiones" in enviado
    assert "no tiene voz" in enviado          # se le avisa al analista
    assert "FOTOGRAMAS CON TIEMPO" in enviado
    assert con.execute("SELECT estado FROM items WHERE id = ?",
                       (item_id,)).fetchone()["estado"] == "analizado"


def test_sin_voz_ni_ocr_no_se_gasta_en_analizar(entorno):
    cfg, con = entorno
    item_id = _item_mudo(con, cfg)
    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    cli = ClienteFalso("{}")
    analyze.procesar(cfg, con, item, cli)

    fila = con.execute("SELECT estado, error FROM items WHERE id = ?", (item_id,)).fetchone()
    assert fila["estado"] == "error_analisis"
    assert "boveda ocr" in fila["error"]
    assert cli.llamadas == 0        # no se llamo a la API


def test_export_incluye_el_texto_en_pantalla(entorno, monkeypatch, tmp_path):
    cfg, con = entorno
    item_id = _item_mudo(con, cfg)
    monkeypatch.setattr(ocr, "extraer_fotogramas",
                        lambda *a, **k: _fotogramas_falsos(cfg, item_id))
    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    ocr.procesar(cfg, con, item, ClienteFalso(json.dumps(LECTURA)))

    destino = tmp_path / "md"
    export.a_markdown(con, destino)
    texto = next(destino.glob("*.md")).read_text(encoding="utf-8")
    assert "## Texto en pantalla" in texto
    assert "comisiones" in texto


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requiere ffmpeg")
def test_extraccion_real_de_fotogramas(entorno, tmp_path):
    """Genera un video sintetico con cambios de plano y comprueba la extraccion."""
    import subprocess

    cfg, con = entorno
    video = tmp_path / "prueba.mp4"
    # tres planos de color distinto, 2 s cada uno
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:d=2",
         "-f", "lavfi", "-i", "color=c=white:s=320x240:d=2",
         "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1[v]", "-map", "[v]",
         "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True,
    )
    fotogramas = ocr.extraer_fotogramas(cfg, video, 99, duracion=6.0)
    assert len(fotogramas) >= 3
    assert all(ruta.stat().st_size > 0 for _, _, ruta in fotogramas)
    assert [t for _, t, _ in fotogramas] == sorted(t for _, t, _ in fotogramas)
