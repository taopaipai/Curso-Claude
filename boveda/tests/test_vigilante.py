"""Pruebas del vigilante.

No se entra en ninguna red real: se sirven paginas locales con la misma forma de
enlaces y se recorren con un navegador de verdad. Eso comprueba todo el camino
—abrir, leer, canonizar, deduplicar, guardar— menos el HTML concreto de cada
plataforma, que solo se puede verificar con una sesion iniciada.
"""

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import config, db, vigilante

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
sin_navegador = pytest.mark.skipif(
    not vigilante.disponible() or not Path(CHROME).exists(),
    reason="requiere playwright y chromium")

PAGINA_IG = """<!doctype html><html><body>
  <a href="https://www.instagram.com/p/AAA111/">uno</a>
  <a href="https://www.instagram.com/reel/BBB222/?igshid=xyz">dos</a>
  <a href="https://www.instagram.com/p/AAA111/">repetido</a>
  <a href="https://www.instagram.com/explore/">ruido que no es un post</a>
  <a href="https://www.instagram.com/otrousuario/">un perfil</a>
</body></html>"""

PAGINA_TT = """<!doctype html><html><body>
  <div data-e2e="favorites-tab" onclick="document.getElementById('favs').hidden=false">
    Favoritos</div>
  <div id="favs" hidden>
    <a href="https://www.tiktok.com/@ana/video/7100000000000000001">a</a>
    <a href="https://www.tiktok.com/@bea/video/7100000000000000002?is_from_webapp=1">b</a>
  </div>
  <a href="https://www.tiktok.com/@ana">perfil, no es un video</a>
</body></html>"""


class Sitio(BaseHTTPRequestHandler):
    paginas: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        cuerpo = self.paginas.get(self.path, "<html><body>vacio</body></html>")
        datos = cuerpo.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


@pytest.fixture()
def sitio():
    Sitio.paginas = {"/ig": PAGINA_IG, "/tt": PAGINA_TT, "/vacio": "<html></html>"}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Sitio)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("BOVEDA_IG_USUARIO", "@yo")
    monkeypatch.setenv("BOVEDA_TIKTOK_USUARIO", "yo")
    # aqui no esta la version de chromium que espera playwright: se usa la que hay
    monkeypatch.setenv("BOVEDA_CHROMIUM", CHROME)
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    return cfg, con


# --- guardado ----------------------------------------------------------------

def test_solo_entra_lo_que_es_una_publicacion(entorno):
    _, con = entorno
    nuevos = vigilante.guardar_enlaces(con, [
        "https://www.instagram.com/p/AAA111/",
        "https://www.instagram.com/reel/BBB222/?igshid=xyz",
        "https://www.instagram.com/explore/",          # no es un post
        "https://www.tiktok.com/@ana/video/1",         # otra red, no toca
    ], "instagram")

    assert nuevos == 2
    urls = [f["url_canonica"] for f in con.execute(
        "SELECT url_canonica FROM items ORDER BY id")]
    assert urls == ["https://www.instagram.com/p/AAA111/",
                    "https://www.instagram.com/p/BBB222/"]


def test_lo_que_ya_estaba_no_se_duplica(entorno):
    _, con = entorno
    enlaces = ["https://www.instagram.com/p/AAA111/"]
    assert vigilante.guardar_enlaces(con, enlaces, "instagram") == 1
    # la segunda ronda ve lo mismo y no anade nada: eso es "solo lo nuevo"
    assert vigilante.guardar_enlaces(con, enlaces, "instagram") == 0
    assert con.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_lo_recogido_queda_en_su_propia_coleccion(entorno):
    _, con = entorno
    vigilante.guardar_enlaces(con, ["https://www.instagram.com/p/AAA111/"], "instagram")
    fila = con.execute(
        "SELECT o.carpeta FROM items i JOIN origenes o ON o.id = i.origen_id").fetchone()
    assert fila["carpeta"] == "vigilante"


def test_el_mismo_enlace_con_tracking_es_el_mismo_guardado(entorno):
    _, con = entorno
    vigilante.guardar_enlaces(con, ["https://www.instagram.com/reel/CCC333/"], "instagram")
    nuevos = vigilante.guardar_enlaces(
        con, ["https://www.instagram.com/reel/CCC333/?igshid=otro"], "instagram")
    assert nuevos == 0


# --- lectura de la pagina con navegador real ---------------------------------

@sin_navegador
def test_lee_los_enlaces_de_una_pagina(entorno, sitio, monkeypatch):
    cfg, _ = entorno
    monkeypatch.setitem(vigilante.PAGINAS, "instagram",
                        {**vigilante.PAGINAS["instagram"], "url": sitio + "/ig",
                         "necesita_usuario": False})
    monkeypatch.setattr(vigilante, "ESPERA_CARGA", 200)

    play, contexto = vigilante._navegador(cfg, visible=False)
    try:
        enlaces = vigilante.revisar_pagina(cfg, contexto, "instagram", profundidad=0)
    finally:
        contexto.close()
        play.stop()

    assert enlaces == ["https://www.instagram.com/p/AAA111/",
                       "https://www.instagram.com/reel/BBB222/?igshid=xyz"]


@sin_navegador
def test_pulsa_la_pestana_de_favoritos(entorno, sitio, monkeypatch):
    """En TikTok los favoritos estan tras un clic: si no se pulsa, no hay nada."""
    cfg, _ = entorno
    monkeypatch.setitem(vigilante.PAGINAS, "tiktok",
                        {**vigilante.PAGINAS["tiktok"], "url": sitio + "/tt",
                         "necesita_usuario": False})
    monkeypatch.setattr(vigilante, "ESPERA_CARGA", 200)

    play, contexto = vigilante._navegador(cfg, visible=False)
    try:
        enlaces = vigilante.revisar_pagina(cfg, contexto, "tiktok", profundidad=0)
    finally:
        contexto.close()
        play.stop()

    assert len(enlaces) == 2
    assert all("/video/" in e for e in enlaces)


@sin_navegador
def test_una_ronda_completa_guarda_lo_nuevo(entorno, sitio, monkeypatch):
    cfg, con = entorno
    monkeypatch.setitem(vigilante.PAGINAS, "instagram",
                        {**vigilante.PAGINAS["instagram"], "url": sitio + "/ig",
                         "necesita_usuario": False})
    monkeypatch.setattr(vigilante, "ESPERA_CARGA", 200)

    primera = vigilante.ronda(cfg, con, ["instagram"], profundidad=0)
    assert primera[0]["vistos"] == 2 and primera[0]["nuevos"] == 2

    segunda = vigilante.ronda(cfg, con, ["instagram"], profundidad=0)
    assert segunda[0]["vistos"] == 2 and segunda[0]["nuevos"] == 0

    historial = vigilante.historial(con)
    assert len(historial) == 2 and historial[0]["nuevos"] == 0


@sin_navegador
def test_una_pagina_vacia_avisa_de_la_sesion(entorno, sitio, monkeypatch):
    """Cero enlaces casi nunca es 'no guardaste nada': suele ser la sesion."""
    cfg, con = entorno
    monkeypatch.setitem(vigilante.PAGINAS, "instagram",
                        {**vigilante.PAGINAS["instagram"], "url": sitio + "/vacio",
                         "necesita_usuario": False})
    monkeypatch.setattr(vigilante, "ESPERA_CARGA", 200)

    resultado = vigilante.ronda(cfg, con, ["instagram"], profundidad=0)[0]
    assert resultado["vistos"] == 0
    assert "sesion" in resultado["error"] and "--login" in resultado["error"]


@sin_navegador
def test_si_una_red_falla_las_demas_siguen(entorno, sitio, monkeypatch):
    cfg, con = entorno
    monkeypatch.setitem(vigilante.PAGINAS, "instagram",
                        {**vigilante.PAGINAS["instagram"], "url": sitio + "/ig",
                         "necesita_usuario": False})
    monkeypatch.setitem(vigilante.PAGINAS, "tiktok",
                        {**vigilante.PAGINAS["tiktok"], "url": sitio + "/vacio",
                         "necesita_usuario": False, "pestana": "#no-existe"})
    monkeypatch.setattr(vigilante, "ESPERA_CARGA", 200)

    resultados = {r["plataforma"]: r for r in
                  vigilante.ronda(cfg, con, ["tiktok", "instagram"], profundidad=0)}
    assert "pestaña de favoritos" in resultados["tiktok"]["error"]
    assert resultados["instagram"]["nuevos"] == 2      # la otra red no se entera


# --- configuracion -----------------------------------------------------------

def test_sin_usuario_configurado_se_dice_cual_falta(entorno, monkeypatch):
    cfg, _ = entorno
    monkeypatch.setenv("BOVEDA_IG_USUARIO", "")
    cfg = config.cargar()
    with pytest.raises(vigilante.ErrorVigilante, match="BOVEDA_IG_USUARIO"):
        vigilante.revisar_pagina(cfg, None, "instagram")


def test_el_arroba_del_usuario_sobra_o_no(monkeypatch, tmp_path):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path))
    monkeypatch.setenv("BOVEDA_IG_USUARIO", "@conarroba")
    monkeypatch.setenv("BOVEDA_TIKTOK_USUARIO", "sinarroba")
    cfg = config.cargar()
    assert cfg.usuarios == {"instagram": "conarroba", "tiktok": "sinarroba"}
