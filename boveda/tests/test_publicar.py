"""Pruebas de la publicacion. Ninguna toca la red: se sustituye `pedir`."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import config, db, publicador
from boveda.cli import main
from boveda.publish import archivo, base, instagram, tiktok, x, youtube
from boveda.publish.base import ErrorRed, Publicacion


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    return cfg, con


def _produccion(con, formato="reel", cuerpo="Primera linea del guion.\n\nSegunda parte."):
    item_id, _ = db.insertar_item(con, {
        "plataforma": "tiktok", "url_canonica": "https://www.tiktok.com/@a/video/1",
        "titulo": "original",
    })
    cur = con.execute(
        "INSERT INTO producciones (item_id, formato, nicho, titulo, cuerpo, modelo) "
        "VALUES (?, ?, 'finanzas', 'Mi guion', ?, 'test')",
        (item_id, formato, cuerpo),
    )
    con.commit()
    return int(cur.lastrowid)


class Espia:
    """Sustituye a `pedir`: devuelve respuestas preparadas y guarda las llamadas."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def __call__(self, url, metodo="GET", **kwargs):
        self.llamadas.append((metodo, url, kwargs))
        if not self.respuestas:
            raise AssertionError(f"llamada inesperada: {metodo} {url}")
        return self.respuestas.pop(0)


# --- reglas de la cola -------------------------------------------------------

def test_no_se_publica_un_borrador(entorno):
    _, con = entorno
    prod = _produccion(con)
    with pytest.raises(publicador.ErrorPublicacion, match="aprobar"):
        publicador.programar(con, prod, "archivo")

    publicador.aprobar(con, prod)
    assert publicador.programar(con, prod, "archivo") > 0


def test_formato_que_no_encaja_en_la_red(entorno):
    _, con = entorno
    prod = _produccion(con, formato="newsletter")
    publicador.aprobar(con, prod)
    with pytest.raises(publicador.ErrorPublicacion, match="no encaja"):
        publicador.programar(con, prod, "instagram")
    assert publicador.programar(con, prod, "instagram", forzar=True) > 0


def test_no_se_publica_dos_veces_en_la_misma_red(entorno):
    cfg, con = entorno
    prod = _produccion(con)
    publicador.aprobar(con, prod)
    pub_id = publicador.programar(con, prod, "archivo")
    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()
    publicador.ejecutar(cfg, con, fila, ensayo=False)

    with pytest.raises(publicador.ErrorPublicacion, match="ya se publico"):
        publicador.programar(con, prod, "archivo")


def test_ensayo_no_cambia_nada(entorno):
    cfg, con = entorno
    prod = _produccion(con)
    publicador.aprobar(con, prod)
    pub_id = publicador.programar(con, prod, "archivo")
    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()

    resultado = publicador.ejecutar(cfg, con, fila, ensayo=True)
    assert "ensayo" in resultado.detalle
    estado = con.execute("SELECT estado, intentos FROM publicaciones WHERE id = ?",
                         (pub_id,)).fetchone()
    assert estado["estado"] == "programada"
    assert estado["intentos"] == 0


def test_publicar_de_verdad_registra_todo(entorno):
    cfg, con = entorno
    prod = _produccion(con)
    publicador.aprobar(con, prod)
    pub_id = publicador.programar(con, prod, "archivo")
    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()

    resultado = publicador.ejecutar(cfg, con, fila, ensayo=False)
    guardado = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()
    assert guardado["estado"] == "publicada"
    assert guardado["publicado_en"] and guardado["id_remoto"]
    assert con.execute("SELECT estado FROM producciones WHERE id = ?",
                       (prod,)).fetchone()["estado"] == "publicado"

    carpeta = cfg.publicaciones / resultado.id_remoto
    assert (carpeta / "texto.md").read_text(encoding="utf-8").startswith("# Mi guion")


def test_un_fallo_de_red_queda_registrado(entorno, monkeypatch):
    cfg, con = entorno
    prod = _produccion(con)
    publicador.aprobar(con, prod)
    pub_id = publicador.programar(con, prod, "archivo")
    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()
    monkeypatch.setattr(archivo, "publicar",
                        lambda *a, **k: (_ for _ in ()).throw(ErrorRed("archivo", "disco lleno")))

    with pytest.raises(ErrorRed):
        publicador.ejecutar(cfg, con, fila, ensayo=False)
    guardado = con.execute("SELECT estado, error, intentos FROM publicaciones WHERE id = ?",
                           (pub_id,)).fetchone()
    assert guardado["estado"] == "error"
    assert "disco lleno" in guardado["error"]
    assert guardado["intentos"] == 1


def test_solo_sale_de_la_cola_lo_que_ya_toca(entorno):
    _, con = entorno
    prod = _produccion(con)
    publicador.aprobar(con, prod)
    publicador.programar(con, prod, "archivo", cuando="2020-01-01T00:00:00+00:00")
    publicador.programar(con, prod, "x", cuando="2099-01-01T00:00:00+00:00", forzar=True)

    assert [f["red"] for f in publicador.pendientes(con)] == ["archivo"]
    assert len(publicador.pendientes(con, incluir_futuras=True)) == 2


# --- conectores --------------------------------------------------------------

def test_instagram_hace_los_tres_pasos(entorno, monkeypatch):
    cfg, con = entorno
    monkeypatch.setenv("IG_USER_ID", "1784100")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "tok")
    espia = Espia([
        {"id": "CONTENEDOR1"},                                  # crear contenedor
        {"status_code": "FINISHED"},                            # procesado
        {"id": "MEDIA9"},                                       # publicar
        {"permalink": "https://www.instagram.com/p/XYZ/"},      # permalink
    ])
    monkeypatch.setattr(instagram, "pedir", espia)
    monkeypatch.setattr(instagram, "ESPERA_PROCESADO", 0)

    pub = Publicacion(produccion_id=1, formato="reel", texto="pie de foto",
                      media_url="https://cdn.mio/reel.mp4")
    resultado = instagram.publicar(cfg, pub)

    assert resultado.id_remoto == "MEDIA9"
    assert resultado.url_remota == "https://www.instagram.com/p/XYZ/"
    metodos = [(m, u.split("?")[0].rsplit("/", 1)[-1]) for m, u, _ in espia.llamadas]
    assert metodos[0] == ("POST", "media")
    assert metodos[2] == ("POST", "media_publish")
    creacion = espia.llamadas[0][2]["form"]
    assert creacion["media_type"] == "REELS"
    assert creacion["video_url"] == "https://cdn.mio/reel.mp4"
    assert espia.llamadas[2][2]["form"]["creation_id"] == "CONTENEDOR1"


def test_instagram_exige_url_publica(entorno, monkeypatch):
    cfg, _ = entorno
    monkeypatch.setenv("IG_USER_ID", "1")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    pub = Publicacion(produccion_id=1, formato="reel", texto="x",
                      media=Path("/tmp/no-importa.mp4"))
    with pytest.raises(ErrorRed, match="URL publica"):
        instagram.publicar(cfg, pub)


def test_instagram_avisa_si_falta_el_token(entorno, monkeypatch):
    cfg, _ = entorno
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("IG_USER_ID", "1")
    with pytest.raises(base.FaltaConfiguracion, match="IG_ACCESS_TOKEN"):
        instagram.publicar(cfg, Publicacion(1, "reel", "x", media_url="https://x/y.mp4"))


def test_tiktok_sube_el_archivo_y_espera_el_estado(entorno, monkeypatch, tmp_path):
    cfg, _ = entorno
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "act")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 2048)

    espia = Espia([
        {"data": {"publish_id": "PUB1", "upload_url": "https://subida.tiktok/1"}},
        {"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["777"]}},
    ])
    subidas = []
    monkeypatch.setattr(tiktok, "pedir", espia)
    monkeypatch.setattr(tiktok, "subir_archivo",
                        lambda url, ruta, **k: subidas.append((url, ruta, k)) or {})
    monkeypatch.setattr(tiktok, "ESPERA_ESTADO", 0)

    resultado = tiktok.publicar(cfg, Publicacion(1, "reel", "mi video", media=video))

    inicio = espia.llamadas[0][2]["json_datos"]
    assert inicio["source_info"] == {"source": "FILE_UPLOAD", "video_size": 2048,
                                     "chunk_size": 2048, "total_chunk_count": 1}
    assert inicio["post_info"]["privacy_level"] == "SELF_ONLY"   # sin auditoria, privado
    assert subidas[0][0] == "https://subida.tiktok/1"
    assert subidas[0][2]["cabeceras"]["Content-Range"] == "bytes 0-2047/2048"
    assert resultado.id_remoto == "777"
    assert "privado" in resultado.detalle


def test_tiktok_falla_con_motivo(entorno, monkeypatch, tmp_path):
    cfg, _ = entorno
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "act")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    monkeypatch.setattr(tiktok, "pedir", Espia([
        {"data": {"publish_id": "P", "upload_url": "u"}},
        {"data": {"status": "FAILED", "fail_reason": "video demasiado corto"}},
    ]))
    monkeypatch.setattr(tiktok, "subir_archivo", lambda *a, **k: {})
    with pytest.raises(ErrorRed, match="demasiado corto"):
        tiktok.publicar(cfg, Publicacion(1, "reel", "x", media=video))


def test_youtube_renueva_token_y_sube(entorno, monkeypatch, tmp_path):
    cfg, _ = entorno
    for clave in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        monkeypatch.setenv(clave, "x")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)

    espia = Espia([
        {"access_token": "ACCESO"},
        {"_headers": {"Location": "https://subida.google/1"}, "_bytes": b"", "_status": 200},
    ])
    monkeypatch.setattr(youtube, "pedir", espia)
    monkeypatch.setattr(youtube, "subir_archivo", lambda url, ruta, **k: {"id": "VID123"})

    resultado = youtube.publicar(cfg, Publicacion(
        1, "short", "# Mi titulo\nel cuerpo", etiquetas=("finanzas",), media=video))

    assert espia.llamadas[0][2]["form"]["grant_type"] == "refresh_token"
    metadatos = espia.llamadas[1][2]["json_datos"]
    assert metadatos["snippet"]["title"] == "Mi titulo"
    assert metadatos["snippet"]["tags"] == ["finanzas"]
    assert metadatos["status"]["privacyStatus"] == "private"   # nunca publico por defecto
    assert resultado.url_remota == "https://www.youtube.com/watch?v=VID123"


def test_x_encadena_el_hilo(entorno, monkeypatch):
    cfg, _ = entorno
    monkeypatch.setenv("X_ACCESS_TOKEN", "tok")
    espia = Espia([{"data": {"id": "1"}}, {"data": {"id": "2"}}, {"data": {"id": "3"}}])
    monkeypatch.setattr(x, "pedir", espia)

    resultado = x.publicar(cfg, Publicacion(
        1, "hilo", "Uno.\n\nDos.\n\nTres."))

    cuerpos = [c[2]["json_datos"] for c in espia.llamadas]
    assert [c["text"] for c in cuerpos] == ["Uno.", "Dos.", "Tres."]
    assert "reply" not in cuerpos[0]
    assert cuerpos[1]["reply"]["in_reply_to_tweet_id"] == "1"
    assert cuerpos[2]["reply"]["in_reply_to_tweet_id"] == "2"
    assert resultado.id_remoto == "1"


def test_trocear_hilo_respeta_parrafos_y_corta_por_frase():
    texto = "Idea corta.\n\n" + "Frase larga que se repite. " * 20 + "\n\nCierre."
    partes = base.trocear_hilo(texto, 280)
    assert partes[0] == "Idea corta."
    assert partes[-1] == "Cierre."
    assert all(len(p) <= 280 for p in partes)
    assert all(p.strip() for p in partes)


# --- CLI ---------------------------------------------------------------------

def test_cli_publicar_sin_confirmar_es_un_ensayo(entorno, capsys):
    cfg, con = entorno
    prod = _produccion(con)
    con.close()

    assert main(["aprobar", str(prod)]) == 0
    assert main(["publicar", str(prod), "--red", "archivo"]) == 0
    salida = capsys.readouterr().out
    assert "ensayo" in salida

    con = db.conectar(cfg.db)
    estados = [f["estado"] for f in con.execute("SELECT estado FROM publicaciones")]
    assert estados == ["cancelada"]         # el ensayo no deja basura en la cola
    assert not any(cfg.publicaciones.iterdir())


def test_cli_publicar_confirmado_deja_el_archivo(entorno, capsys):
    cfg, con = entorno
    prod = _produccion(con)
    con.close()

    assert main(["aprobar", str(prod)]) == 0
    assert main(["publicar", str(prod), "--red", "archivo", "--confirmar"]) == 0
    assert main(["publicaciones"]) == 0
    salida = capsys.readouterr().out
    assert "publicada" in salida
    assert any((cfg.publicaciones).iterdir())


def test_cli_redes_lista_lo_configurado(entorno, monkeypatch, capsys):
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    assert main(["redes"]) == 0
    salida = capsys.readouterr().out
    assert "archivo" in salida and "configurada" in salida
    assert "instagram" in salida and "sin credenciales" in salida


def test_la_misma_produccion_puede_salir_en_varias_redes(entorno):
    cfg, con = entorno
    prod = _produccion(con)
    publicador.aprobar(con, prod)
    pub_id = publicador.programar(con, prod, "archivo")
    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()
    publicador.ejecutar(cfg, con, fila, ensayo=False)

    # ya publicada en 'archivo', pero tiktok sigue siendo un destino valido
    assert publicador.programar(con, prod, "tiktok") > 0
