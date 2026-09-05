"""Prueba el flujo completo sin tocar la red: importar -> transcribir -> analizar."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import config, db, export
from boveda.cli import main
from boveda.pipeline import analyze, repurpose

ANALISIS = {
    "tipo_contenido": "instructivo",
    "nicho": "productividad",
    "tema_principal": "Como ordenar tu bandeja de entrada",
    "subtemas": ["email", "habitos"],
    "hook": {"texto": "Tu bandeja no es una lista de tareas",
             "tecnica": "contradiccion de creencia", "segundos": 1.5,
             "por_que_funciona": "rompe un habito que el espectador reconoce como propio"},
    "estructura": [{"seccion": "gancho", "inicio_seg": 0, "fin_seg": 3,
                    "proposito": "detener el scroll", "texto_clave": "no es una lista"}],
    "por_que_funciona": {"factores": ["contradiccion", "beneficio inmediato"],
                         "emocion_dominante": "alivio", "promesa": "bandeja a cero",
                         "tension": "acumulacion", "resolucion": "regla de dos minutos"},
    "ganchos_reutilizables": ["X no es lo que crees"],
    "datos_y_afirmaciones": [{"afirmacion": "el 80% revisa el correo al despertar",
                              "verificable": False, "contexto_temporal": "sin fecha"}],
    "vigencia": {"estado": "atemporal", "razon": "principio de habitos", "valor_historico": 4},
    "aplicabilidad": {"para_nosotros": ["adaptar a gestion de DMs"],
                      "para_ensenar": ["ensenar la regla de dos minutos"],
                      "formatos_sugeridos": ["reel", "carrusel"]},
    "cta": "Guarda esto para el lunes",
    "etiquetas": ["productividad", "email"],
    "calidad_transcripcion": "buena",
}


class ClienteFalso:
    """Sustituye a anthropic.Anthropic devolviendo respuestas fijas."""

    def __init__(self, texto):
        self.texto = texto
        self.llamadas = 0
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.llamadas += 1
        self.ultimo = kwargs
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=self.texto)],
        )


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    return cfg, con


def _item_transcrito(con, texto="Tu bandeja no es una lista de tareas. Usa la regla de dos minutos."):
    origen = db.obtener_origen(con, "youtube", "productividad")
    item_id, nuevo = db.insertar_item(con, {
        "origen_id": origen, "plataforma": "youtube",
        "url_canonica": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "id_externo": "dQw4w9WgXcQ", "autor": "Canal X", "titulo": "Bandeja a cero",
        "descripcion": None, "duracion_seg": 60, "publicado_en": "2019-05-01",
        "guardado_en": "2020-01-01", "idioma": "es", "metricas_json": None, "crudo_json": None,
    })
    assert nuevo
    con.execute(
        "INSERT INTO transcripciones (item_id, motor, idioma, texto, segmentos_json) "
        "VALUES (?, 'test', 'es', ?, ?)",
        (item_id, texto, json.dumps([{"inicio": 0, "fin": 3, "texto": texto}])),
    )
    db.marcar(con, item_id, "transcrito")
    return item_id


def test_deduplicacion_por_url(entorno):
    _, con = entorno
    item_id = _item_transcrito(con)
    repetido, nuevo = db.insertar_item(con, {
        "plataforma": "youtube",
        "url_canonica": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    })
    assert (repetido, nuevo) == (item_id, False)


def test_analisis_guarda_columnas_y_etiquetas(entorno):
    cfg, con = entorno
    item_id = _item_transcrito(con)
    cli = ClienteFalso(json.dumps(ANALISIS))

    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    analyze.procesar(cfg, con, item, cli)

    fila = con.execute("SELECT * FROM analisis WHERE item_id = ?", (item_id,)).fetchone()
    assert fila["tipo_contenido"] == "instructivo"
    assert fila["vigencia_estado"] == "atemporal"
    assert fila["valor_historico"] == 4
    assert con.execute("SELECT estado FROM items WHERE id = ?",
                       (item_id,)).fetchone()["estado"] == "analizado"
    etiquetas = {f["etiqueta"] for f in
                 con.execute("SELECT etiqueta FROM etiquetas WHERE item_id = ?", (item_id,))}
    assert etiquetas == {"productividad", "email"}
    # el esquema estructurado viaja en la peticion
    assert cli.ultimo["output_config"]["format"]["type"] == "json_schema"


def test_busqueda_encuentra_por_transcripcion_y_por_analisis(entorno):
    cfg, con = entorno
    item_id = _item_transcrito(con)
    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    analyze.procesar(cfg, con, item, ClienteFalso(json.dumps(ANALISIS)))

    assert db.buscar(con, "minutos")[0]["item_id"] == item_id
    assert db.buscar(con, "contradiccion")[0]["item_id"] == item_id
    assert db.buscar(con, "palabraquenoexiste") == []


def test_analisis_invalido_deja_el_item_en_error(entorno):
    cfg, con = entorno
    item_id = _item_transcrito(con)
    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    analyze.procesar(cfg, con, item, ClienteFalso("esto no es json"))

    fila = con.execute("SELECT estado, error FROM items WHERE id = ?", (item_id,)).fetchone()
    assert fila["estado"] == "error_analisis"
    assert fila["error"]


def test_produccion_y_export_markdown(entorno, tmp_path):
    cfg, con = entorno
    item_id = _item_transcrito(con)
    item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    analyze.procesar(cfg, con, item, ClienteFalso(json.dumps(ANALISIS)))

    prod_id = repurpose.generar(cfg, con, item_id, "carrusel", nicho="finanzas",
                                cli=ClienteFalso("Lamina 1: tu dinero no es una lista"))
    assert prod_id > 0

    destino = tmp_path / "md"
    assert export.a_markdown(con, destino) == 1
    texto = next(destino.glob("*.md")).read_text(encoding="utf-8")
    assert "vigencia: atemporal" in texto
    assert "Produccion — carrusel" in texto
    assert "regla de dos minutos" in texto


def test_produccion_exige_analisis_previo(entorno):
    cfg, con = entorno
    item_id = _item_transcrito(con)
    with pytest.raises(LookupError):
        repurpose.generar(cfg, con, item_id, "reel", cli=ClienteFalso("x"))


def test_cli_importar_y_estado(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    enlaces = tmp_path / "links.txt"
    enlaces.write_text("# virales\nhttps://www.tiktok.com/@ana/video/1\n", encoding="utf-8")

    assert main(["init"]) == 0
    assert main(["importar", "urls", str(enlaces)]) == 0
    assert main(["estado"]) == 0
    salida = capsys.readouterr().out
    assert "1 nuevos" in salida
    assert "importado" in salida
