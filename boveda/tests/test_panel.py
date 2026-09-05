"""Pruebas del panel: se levanta el servidor de verdad y se le hacen peticiones."""

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import config, db
from boveda.panel import consultas
from boveda.panel.servidor import Panel


@pytest.fixture()
def servidor(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    _poblar(con)

    Panel.cfg = cfg
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Panel)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, con, cfg
    httpd.shutdown()
    httpd.server_close()


def _poblar(con):
    origen = db.obtener_origen(con, "youtube", "finanzas")
    uno, _ = db.insertar_item(con, {
        "origen_id": origen, "plataforma": "youtube",
        "url_canonica": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "titulo": "Bandeja a cero", "autor": "Canal X"})
    con.execute("UPDATE items SET estado='analizado', vistas=1000, likes=90 WHERE id=?", (uno,))
    con.execute("INSERT INTO analisis (item_id, modelo, tipo_contenido, nicho,"
                " vigencia_estado, valor_historico, analisis_json)"
                " VALUES (?, 'm', 'instructivo', 'finanzas', 'atemporal', 5, ?)",
                (uno, json.dumps({"hook": {"texto": "un gancho"}})))
    con.execute("INSERT INTO comentarios (item_id, posicion, texto, likes, segundos_tras,"
                " tiempo_exacto) VALUES (?, 1, 'gran consejo', 500, 3600, 1)", (uno,))

    origen2 = db.obtener_origen(con, "tiktok", "cocina")
    dos, _ = db.insertar_item(con, {
        "origen_id": origen2, "plataforma": "tiktok",
        "url_canonica": "https://www.tiktok.com/@a/video/1", "titulo": "Receta rapida"})
    con.execute("UPDATE items SET estado='error_descarga', error='HTTP 404' WHERE id=?", (dos,))

    con.execute("INSERT INTO producciones (item_id, formato, nicho, titulo, cuerpo, modelo,"
                " estado) VALUES (?, 'carrusel', 'finanzas', 'Mi carrusel', 'cuerpo', 'm',"
                " 'borrador')", (uno,))
    con.execute("INSERT INTO producciones (item_id, formato, nicho, titulo, cuerpo, modelo,"
                " estado) VALUES (?, 'reel', 'finanzas', 'Mi reel', 'cuerpo', 'm', 'aprobado')",
                (uno,))
    con.execute("INSERT INTO publicaciones (produccion_id, red, estado) VALUES (2, 'tiktok',"
                " 'programada')")
    con.commit()


def _get(base, ruta):
    with urllib.request.urlopen(base + ruta, timeout=10) as r:
        return json.loads(r.read())


def _post(base, datos):
    peticion = urllib.request.Request(
        base + "/api/accion", data=json.dumps(datos).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# --- tablero -----------------------------------------------------------------

def test_el_tablero_trae_todas_las_etapas(servidor):
    base, _, _ = servidor
    datos = _get(base, "/api/tablero")
    columnas = {c["id"]: c for c in datos["columnas"]}

    assert [c["id"] for c in consultas.COLUMNAS] == [
        "importado", "descargado", "transcrito", "analizado",
        "borrador", "aprobado", "programada", "publicado"]
    assert columnas["analizado"]["total"] == 1
    assert columnas["borrador"]["total"] == 1
    assert columnas["aprobado"]["total"] == 1
    assert columnas["programada"]["total"] == 1
    assert columnas["errores"]["total"] == 1        # la columna de errores solo sale si hay
    assert datos["resumen"] == {"items": 2, "analizados": 1, "publicados": 0, "comentarios": 1}


def test_la_tarjeta_lleva_lo_que_hace_falta_para_decidir(servidor):
    base, _, _ = servidor
    tarjeta = next(c for c in _get(base, "/api/tablero")["columnas"]
                   if c["id"] == "analizado")["tarjetas"][0]
    assert tarjeta["titulo"] == "Bandeja a cero"
    assert tarjeta["plataforma"] == "youtube"
    assert tarjeta["vigencia"] == "atemporal" and tarjeta["valor"] == 5
    assert tarjeta["metricas"]["vistas"] == 1000
    assert tarjeta["n_comentarios"] == 1
    assert tarjeta["miniatura"] is None              # sin fotogramas, sin peticion inutil


def test_los_filtros_recortan_el_tablero(servidor):
    base, _, _ = servidor

    def totales(consulta):
        return {c["id"]: c["total"] for c in _get(base, "/api/tablero" + consulta)["columnas"]}

    assert totales("?plataforma=tiktok").get("analizado") == 0
    assert totales("?plataforma=youtube")["analizado"] == 1
    assert totales("?carpeta=cocina")["errores"] == 1
    assert totales("?q=bandeja")["analizado"] == 1
    assert totales("?q=noexiste").get("analizado") == 0


def test_las_opciones_de_filtro_salen_de_los_datos(servidor):
    base, _, _ = servidor
    filtros = _get(base, "/api/tablero")["filtros"]
    assert filtros["plataformas"] == ["tiktok", "youtube"]
    assert filtros["carpetas"] == ["cocina", "finanzas"]


# --- ficha -------------------------------------------------------------------

def test_la_ficha_junta_todo_lo_del_item(servidor):
    base, _, _ = servidor
    d = _get(base, "/api/item/1")
    assert d["item"]["titulo"] == "Bandeja a cero"
    assert d["analisis"]["hook"]["texto"] == "un gancho"
    assert d["comentarios"][0]["texto"] == "gran consejo"
    assert len(d["producciones"]) == 2
    assert d["publicaciones"][0]["red"] == "tiktok"
    assert "crudo_json" not in d["item"]          # el volcado crudo no viaja al navegador


def test_un_item_que_no_existe_da_404(servidor):
    base, _, _ = servidor
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base, "/api/item/999")
    assert exc.value.code == 404


def test_la_pagina_se_sirve_en_la_raiz(servidor):
    base, _, _ = servidor
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        html = r.read().decode("utf-8")
    assert "<title>Bóveda</title>" in html
    assert "text/html" in r.headers.get("Content-Type", "")


# --- acciones ----------------------------------------------------------------

def test_aprobar_y_devolver_una_produccion(servidor):
    base, con, _ = servidor
    codigo, r = _post(base, {"accion": "aprobar", "id": 1})
    assert codigo == 200 and r["ok"]
    assert con.execute("SELECT estado FROM producciones WHERE id=1").fetchone()[0] == "aprobado"

    _post(base, {"accion": "devolver", "id": 1})
    assert con.execute("SELECT estado FROM producciones WHERE id=1").fetchone()[0] == "borrador"


def test_reintentar_devuelve_el_item_a_su_etapa(servidor):
    base, con, _ = servidor
    codigo, r = _post(base, {"accion": "reintentar", "id": 2})
    assert codigo == 200 and r["ok"]
    fila = con.execute("SELECT estado, error FROM items WHERE id=2").fetchone()
    assert fila["estado"] == "importado" and fila["error"] is None


def test_no_se_reintenta_lo_que_no_esta_en_error(servidor):
    base, _, _ = servidor
    codigo, r = _post(base, {"accion": "reintentar", "id": 1})
    assert codigo == 400 and "no esta en error" in r["error"]


def test_cancelar_saca_de_la_cola(servidor):
    base, con, _ = servidor
    codigo, _ = _post(base, {"accion": "cancelar", "id": 1})
    assert codigo == 200
    assert con.execute("SELECT estado FROM publicaciones WHERE id=1").fetchone()[0] == "cancelada"


def test_el_panel_no_publica(servidor):
    """Publicar no se puede deshacer: no se hace con un clic desde el navegador."""
    base, con, _ = servidor
    codigo, r = _post(base, {"accion": "publicar", "id": 2})
    assert codigo == 400 and "accion desconocida" in r["error"]
    assert con.execute(
        "SELECT COUNT(*) FROM publicaciones WHERE estado='publicada'").fetchone()[0] == 0


def test_una_peticion_rota_no_tumba_el_panel(servidor):
    base, _, _ = servidor
    peticion = urllib.request.Request(
        base + "/api/accion", data=b"esto no es json",
        headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(peticion, timeout=10)
    assert exc.value.code == 400
    assert _get(base, "/api/tablero")["resumen"]["items"] == 2   # sigue en pie


# --- miniaturas --------------------------------------------------------------

def test_la_miniatura_sale_del_fotograma_guardado(servidor, tmp_path):
    base, con, cfg = servidor
    imagen = tmp_path / "f.jpg"
    imagen.write_bytes(b"\xff\xd8\xff jpeg")
    con.execute("INSERT INTO fotogramas (item_id, indice, segundo, ruta) VALUES (1, 0, 0, ?)",
                (str(imagen),))
    con.commit()

    with urllib.request.urlopen(base + "/miniatura/1", timeout=10) as r:
        assert r.read() == b"\xff\xd8\xff jpeg"

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(base + "/miniatura/2", timeout=10)
    assert exc.value.code == 404
