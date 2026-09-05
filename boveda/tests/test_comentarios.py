"""Pruebas de la captura de comentarios y metricas. Sin red: yt-dlp simulado."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import comentarios as com
from boveda import config, db
from boveda.cli import main
from boveda.pipeline import download

# 15 de enero de 2020, 00:00 UTC
PUBLICADO = 1579046400


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    return cfg, con


def _item(con, plataforma="youtube", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
    item_id, _ = db.insertar_item(con, {"plataforma": plataforma, "url_canonica": url})
    return item_id


# --- metricas ----------------------------------------------------------------

def test_metricas_entiende_los_alias_de_cada_extractor():
    assert com.metricas({"view_count": 10, "like_count": 2, "comment_count": 1}) == {
        "vistas": 10, "likes": 2, "comentarios": 1, "compartidos": None, "guardados": None}
    # TikTok usa otros nombres y es la unica que da guardados
    assert com.metricas({"play_count": 99, "digg_count": 8, "share_count": 3,
                         "collect_count": 7, "comment_count": 4}) == {
        "vistas": 99, "likes": 8, "comentarios": 4, "compartidos": 3, "guardados": 7}


def test_momento_de_publicacion_desde_fecha_o_epoch():
    assert com.momento_publicacion({"timestamp": 1234567890}) == 1234567890
    assert com.momento_publicacion({"upload_date": "20200115"}) == PUBLICADO
    assert com.momento_publicacion({"upload_date": "regular"}) is None
    assert com.momento_publicacion({}) is None


def test_cada_captura_deja_una_instantanea(entorno):
    _, con = entorno
    item_id = _item(con)
    com.guardar_metricas(con, item_id, {"vistas": 100, "likes": 10, "comentarios": 2,
                                        "compartidos": None, "guardados": None})
    com.guardar_metricas(con, item_id, {"vistas": 250, "likes": 30, "comentarios": 5,
                                        "compartidos": None, "guardados": None})

    historial = con.execute(
        "SELECT vistas FROM metricas WHERE item_id = ? ORDER BY id", (item_id,)).fetchall()
    assert [f["vistas"] for f in historial] == [100, 250]     # se conserva la historia
    fila = con.execute("SELECT vistas, likes FROM items WHERE id = ?", (item_id,)).fetchone()
    assert (fila["vistas"], fila["likes"]) == (250, 30)       # y el ultimo valor a mano


# --- normalizacion -----------------------------------------------------------

def test_se_queda_con_los_mas_votados_y_tira_las_respuestas():
    crudos = [
        {"id": "a", "text": "poco votado", "like_count": 2, "parent": "root"},
        {"id": "b", "text": "el mas votado", "like_count": 900, "parent": "root"},
        {"id": "c", "text": "una respuesta", "like_count": 5000, "parent": "b"},
    ]
    salida = com.normalizar(crudos, "youtube", PUBLICADO)
    assert [c["texto"] for c in salida] == ["el mas votado", "poco votado"]
    assert [c["posicion"] for c in salida] == [1, 2]


def test_se_respeta_el_tope_de_veinte():
    crudos = [{"id": str(i), "text": f"c{i}", "like_count": i, "parent": "root"}
              for i in range(50)]
    assert len(com.normalizar(crudos, "youtube", PUBLICADO)) == com.TOPE == 20


def test_calcula_cuanto_despues_del_video_se_comento():
    crudos = [{"id": "a", "text": "x", "like_count": 1, "parent": "root",
               "timestamp": PUBLICADO + 7200}]
    salida = com.normalizar(crudos, "instagram", PUBLICADO)
    assert salida[0]["segundos_tras"] == 7200
    assert salida[0]["tiempo_exacto"] == 1
    assert com.humanizar(7200) == "2 h"


def test_en_youtube_el_tiempo_se_marca_como_estimado():
    crudos = [{"id": "a", "text": "x", "like_count": 1, "parent": "root",
               "timestamp": PUBLICADO + 86400}]
    assert com.normalizar(crudos, "youtube", PUBLICADO)[0]["tiempo_exacto"] == 0


def test_un_comentario_anterior_al_video_no_se_guarda_como_tiempo():
    # la estimacion de YouTube puede pasarse de largo; mejor sin dato que con uno falso
    crudos = [{"id": "a", "text": "x", "like_count": 1, "parent": "root",
               "timestamp": PUBLICADO - 5000}]
    assert com.normalizar(crudos, "youtube", PUBLICADO)[0]["segundos_tras"] is None


def test_sin_fecha_de_publicacion_no_se_inventa_el_retraso():
    crudos = [{"id": "a", "text": "x", "like_count": 1, "parent": "root",
               "timestamp": PUBLICADO}]
    assert com.normalizar(crudos, "instagram", None)[0]["segundos_tras"] is None


def test_los_comentarios_vacios_se_descartan():
    crudos = [{"id": "a", "text": "   ", "like_count": 99, "parent": "root"},
              {"id": "b", "text": "vale", "like_count": 1, "parent": "root"}]
    assert [c["texto"] for c in com.normalizar(crudos, "youtube", PUBLICADO)] == ["vale"]


def test_humanizar_cubre_toda_la_escala():
    assert [com.humanizar(v) for v in (None, 120, 7200, 172800, 5184000, 63072000)] == [
        "?", "2 min", "2 h", "2 d", "2 meses", "2 años"]


# --- guardado ----------------------------------------------------------------

def test_guardar_reemplaza_la_tanda_anterior(entorno):
    _, con = entorno
    item_id = _item(con)
    com.guardar(con, item_id, com.normalizar(
        [{"id": "a", "text": "viejo", "like_count": 1, "parent": "root"}], "youtube", None))
    com.guardar(con, item_id, com.normalizar(
        [{"id": "b", "text": "nuevo", "like_count": 9, "parent": "root"},
         {"id": "c", "text": "otro", "like_count": 3, "parent": "root"}], "youtube", None))

    filas = con.execute("SELECT texto FROM comentarios WHERE item_id = ? ORDER BY posicion",
                        (item_id,)).fetchall()
    assert [f["texto"] for f in filas] == ["nuevo", "otro"]


def test_tiktok_no_soporta_comentarios():
    assert com.soporta_comentarios("tiktok") is False
    assert com.soporta_comentarios("youtube") is True
    assert com.CAPACIDADES["tiktok"]["guardados"] is True


# --- integracion con la descarga --------------------------------------------

def test_la_descarga_guarda_ficha_metricas_y_comentarios(entorno, monkeypatch, tmp_path):
    cfg, con = entorno
    item_id = _item(con)
    ficha = {
        "title": "Bandeja a cero", "uploader": "Canal X", "description": "desc",
        "duration": 61, "upload_date": "20200115", "view_count": 5000,
        "like_count": 300, "comment_count": 42, "language": "es",
        "formats": [{"url": "no queremos esto"}],
        "comments": [
            {"id": "1", "text": "el mejor consejo", "like_count": 120, "parent": "root",
             "timestamp": PUBLICADO + 3600},
            {"id": "2", "text": "no funciona", "like_count": 5, "parent": "root",
             "timestamp": PUBLICADO + 86400},
        ],
    }
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(download, "metadatos", lambda cfg, url, plataforma=None: ficha)
    monkeypatch.setattr(download, "descargar_audio", lambda *a, **k: audio)

    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    download.procesar(cfg, con, item)

    fila = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    assert fila["estado"] == "descargado"
    assert (fila["vistas"], fila["likes"], fila["comentarios_n"]) == (5000, 300, 42)
    assert fila["publicado_ts"] == PUBLICADO
    # la ficha se guarda entera menos el ruido pesado
    guardada = json.loads(fila["metricas_json"])
    assert guardada["title"] == "Bandeja a cero" and "formats" not in guardada

    comentarios = con.execute(
        "SELECT * FROM comentarios WHERE item_id = ? ORDER BY posicion", (item_id,)).fetchall()
    assert [c["texto"] for c in comentarios] == ["el mejor consejo", "no funciona"]
    assert comentarios[0]["segundos_tras"] == 3600


def test_youtube_pide_los_comentarios_por_votos(entorno, monkeypatch):
    cfg, _ = entorno
    ordenes = []

    class Salida:
        stdout = "{}"

    monkeypatch.setattr(download.subprocess, "run",
                        lambda cmd, **k: ordenes.append(cmd) or Salida())

    download.metadatos(cfg, "https://youtu.be/x", "youtube")
    assert "--write-comments" in ordenes[0]
    assert "youtube:comment_sort=top;max_comments=20,20,0,0" in ordenes[0]

    download.metadatos(cfg, "https://www.tiktok.com/@a/video/1", "tiktok")
    assert "--write-comments" not in ordenes[1]   # TikTok no los da: no se piden


def test_refrescar_anade_instantanea_sin_volver_a_descargar(entorno, monkeypatch):
    cfg, con = entorno
    item_id = _item(con)
    monkeypatch.setattr(download, "metadatos", lambda cfg, url, plataforma=None: {
        "upload_date": "20200115", "view_count": 9999, "like_count": 800,
        "comments": [{"id": "z", "text": "nuevo comentario", "like_count": 7,
                      "parent": "root", "timestamp": PUBLICADO + 60}],
    })
    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    assert download.refrescar(cfg, con, item) == 1
    assert con.execute("SELECT vistas FROM items WHERE id = ?",
                       (item_id,)).fetchone()["vistas"] == 9999
    assert con.execute("SELECT COUNT(*) n FROM metricas").fetchone()["n"] == 1


def test_el_analisis_recibe_los_comentarios(entorno, monkeypatch):
    from boveda.pipeline import analyze
    from tests.test_flujo import ANALISIS, ClienteFalso
    cfg, con = entorno
    item_id = _item(con)
    con.execute("INSERT INTO transcripciones (item_id, motor, texto) VALUES (?, 't', 'hola')",
                (item_id,))
    com.guardar(con, item_id, com.normalizar(
        [{"id": "a", "text": "¿y si no tengo tiempo?", "like_count": 500, "parent": "root",
          "timestamp": PUBLICADO + 3600}], "instagram", PUBLICADO))
    db.marcar(con, item_id, "transcrito")

    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    cli = ClienteFalso(json.dumps(ANALISIS))
    analyze.procesar(cfg, con, item, cli)

    enviado = cli.ultimo["messages"][0]["content"]
    assert "COMENTARIOS MAS VOTADOS" in enviado
    assert "¿y si no tengo tiempo?" in enviado
    assert "500 likes" in enviado


# --- CLI ---------------------------------------------------------------------

def test_cli_comentarios_muestra_la_lista(entorno, capsys):
    cfg, con = entorno
    item_id = _item(con)
    con.execute("UPDATE items SET titulo = 'Bandeja', vistas = 5000, likes = 300 WHERE id = ?",
                (item_id,))
    com.guardar(con, item_id, com.normalizar(
        [{"id": "a", "text": "el mejor consejo", "like_count": 120, "parent": "root",
          "timestamp": PUBLICADO + 3600}], "instagram", PUBLICADO))
    con.commit()
    con.close()

    assert main(["comentarios", str(item_id)]) == 0
    salida = capsys.readouterr().out
    assert "el mejor consejo" in salida and "120 likes" in salida and "1 h" in salida


def test_cli_explica_por_que_tiktok_no_tiene_comentarios(entorno, capsys):
    cfg, con = entorno
    item_id = _item(con, "tiktok", "https://www.tiktok.com/@a/video/1")
    con.commit()
    con.close()

    assert main(["comentarios", str(item_id)]) == 0
    assert "yt-dlp no extrae comentarios de TikTok" in capsys.readouterr().out
