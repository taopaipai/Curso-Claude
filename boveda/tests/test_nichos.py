"""Pruebas de los nichos: montaje, cuentas, credenciales por perfil y reparto."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from boveda import config, db, nichos, publicador
from boveda.publish import base as pbase
from boveda.publish import instagram


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("BOVEDA_HOME", str(tmp_path / "data"))
    cfg = config.cargar()
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    return cfg, con


def _produccion(con, formato="reel"):
    item_id, _ = db.insertar_item(con, {"plataforma": "tiktok",
                                        "url_canonica": "https://t/1"})
    cur = con.execute(
        "INSERT INTO producciones (item_id, formato, nicho, titulo, cuerpo, modelo, estado)"
        " VALUES (?, ?, 'marketing', 'T', 'cuerpo', 'm', 'aprobado')", (item_id, formato))
    con.commit()
    return int(cur.lastrowid)


# --- crear y montar ----------------------------------------------------------

def test_crear_siembra_el_kanban_de_montaje(entorno):
    _, con = entorno
    nichos.crear(con, "Marketing Directo")
    nicho = nichos.obtener(con, "marketing-directo")     # la clave se normaliza
    assert nicho["nombre"] == "Marketing Directo"

    tareas = nichos.tareas(con, int(nicho["id"]))
    assert len(tareas) == len(nichos.PLANTILLA)
    assert {t["etapa"] for t in tareas} == {e["id"] for e in nichos.ETAPAS}
    # los trámites lentos de cada plataforma están en la plantilla
    titulos = " ".join(t["titulo"] for t in tareas)
    assert "auditoría" in titulos and "página de Facebook" in titulos


def test_no_se_repite_un_nicho(entorno):
    _, con = entorno
    nichos.crear(con, "ia")
    with pytest.raises(nichos.ErrorNicho, match="ya existe"):
        nichos.crear(con, "IA")                          # misma clave normalizada


def test_una_clave_vacia_no_vale(entorno):
    _, con = entorno
    with pytest.raises(nichos.ErrorNicho, match="vacía"):
        nichos.crear(con, "   ")


def test_el_perfil_de_credenciales_sale_de_la_clave():
    assert nichos.perfil_env("marketing-directo") == "MARKETING_DIRECTO"
    assert nichos.perfil_env("ia") == "IA"


def test_el_progreso_senala_la_primera_etapa_sin_terminar(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    nicho = nichos.obtener(con, "marketing")
    tareas = nichos.tareas(con, int(nicho["id"]))
    assert nichos.progreso(con, int(nicho["id"]))["etapa_actual"] == "definicion"

    for tarea in [t for t in tareas if t["etapa"] == "definicion"]:
        nichos.marcar_tarea(con, tarea["id"])
    datos = nichos.progreso(con, int(nicho["id"]))
    assert datos["etapa_actual"] == "marca"
    assert datos["porcentaje"] == round(100 * 3 / len(nichos.PLANTILLA))


def test_marcar_y_desmarcar_una_tarea(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    tarea = nichos.tareas(con, int(nichos.obtener(con, "marketing")["id"]))[0]

    nichos.marcar_tarea(con, tarea["id"])
    fila = con.execute("SELECT hecha, completado_en FROM tareas_nicho WHERE id=?",
                       (tarea["id"],)).fetchone()
    assert fila["hecha"] == 1 and fila["completado_en"]

    nichos.marcar_tarea(con, tarea["id"], False)
    fila = con.execute("SELECT hecha, completado_en FROM tareas_nicho WHERE id=?",
                       (tarea["id"],)).fetchone()
    assert fila["hecha"] == 0 and fila["completado_en"] is None


# --- cuentas -----------------------------------------------------------------

def test_una_cuenta_por_red_y_nicho(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    uno = nichos.anadir_cuenta(con, "marketing", "instagram", "@marca")
    dos = nichos.anadir_cuenta(con, "marketing", "instagram", estrategia="1 reel al día")
    assert uno == dos                                   # es la misma cuenta, actualizada

    cuenta = nichos.cuentas(con, int(nichos.obtener(con, "marketing")["id"]))[0]
    assert cuenta["handle"] == "@marca"                  # no se pisa con vacío
    assert cuenta["estrategia"] == "1 reel al día"


def test_no_se_aceptan_redes_ni_etapas_inventadas(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    with pytest.raises(nichos.ErrorNicho, match="red desconocida"):
        nichos.anadir_cuenta(con, "marketing", "myspace")
    with pytest.raises(nichos.ErrorNicho, match="etapa de cuenta"):
        nichos.anadir_cuenta(con, "marketing", "tiktok", etapa="casi")


def test_verificar_una_cuenta_deja_la_fecha(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    cuenta_id = nichos.anadir_cuenta(con, "marketing", "tiktok")
    nichos.mover_cuenta(con, cuenta_id, "verificada")
    fila = con.execute("SELECT etapa, verificado_en FROM cuentas WHERE id=?",
                       (cuenta_id,)).fetchone()
    assert fila["etapa"] == "verificada" and fila["verificado_en"]


def test_solo_publican_las_cuentas_con_credenciales(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    nichos.anadir_cuenta(con, "marketing", "instagram", etapa="creada")
    nichos.anadir_cuenta(con, "marketing", "tiktok", etapa="token")
    nichos.anadir_cuenta(con, "marketing", "youtube", etapa="verificada")

    redes = [c["red"] for c in nichos.redes_publicables(con, "marketing")]
    assert redes == ["tiktok", "youtube"]                # instagram aún no está lista


# --- credenciales por perfil -------------------------------------------------

def test_gana_la_credencial_del_nicho_sobre_la_general(monkeypatch):
    monkeypatch.setenv("IG_ACCESS_TOKEN", "general")
    monkeypatch.setenv("IG_ACCESS_TOKEN__MARKETING", "de-marketing")
    assert pbase.env("IG_ACCESS_TOKEN", "instagram", "MARKETING") == "de-marketing"
    assert pbase.env("IG_ACCESS_TOKEN", "instagram", "IA") == "general"
    assert pbase.env("IG_ACCESS_TOKEN", "instagram") == "general"


def test_sin_credencial_el_error_dice_que_variable_falta(monkeypatch):
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("IG_ACCESS_TOKEN__IA", raising=False)
    with pytest.raises(pbase.FaltaConfiguracion, match="IG_ACCESS_TOKEN__IA"):
        pbase.env("IG_ACCESS_TOKEN", "instagram", "IA")


def test_cada_nicho_ve_sus_propias_credenciales(monkeypatch, entorno):
    cfg, _ = entorno
    monkeypatch.delenv("IG_USER_ID", raising=False)
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("IG_USER_ID__MARKETING", "1784")
    monkeypatch.setenv("IG_ACCESS_TOKEN__MARKETING", "tok")
    assert instagram.configurada(cfg, "MARKETING") is True
    assert instagram.configurada(cfg, "IA") is False


def test_el_conector_usa_el_token_del_nicho(monkeypatch, entorno):
    cfg, _ = entorno
    monkeypatch.setenv("IG_USER_ID__MARKETING", "1784")
    monkeypatch.setenv("IG_ACCESS_TOKEN__MARKETING", "tok-marketing")
    llamadas = []
    monkeypatch.setattr(instagram, "pedir",
                        lambda url, metodo="GET", **k: llamadas.append((url, k)) or
                        ({"id": "C1"} if "media" in url and metodo == "POST" else
                         {"status_code": "FINISHED", "id": "M1", "permalink": ""}))

    instagram.publicar(cfg, pbase.Publicacion(
        1, "reel", "texto", media_url="https://cdn/x.mp4", perfil="MARKETING"))
    assert llamadas[0][1]["form"]["access_token"] == "tok-marketing"


# --- reparto en todas las redes del nicho ------------------------------------

def test_distribuir_programa_una_publicacion_por_cuenta(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    nichos.anadir_cuenta(con, "marketing", "tiktok", "@mk", etapa="verificada")
    nichos.anadir_cuenta(con, "marketing", "youtube", "@mkyt", etapa="token")
    nichos.anadir_cuenta(con, "marketing", "instagram", "@mkig", etapa="creada")
    prod = _produccion(con, "reel")

    repartos = publicador.distribuir(con, prod, "marketing")
    assert [r["red"] for r in repartos if r["ok"]] == ["tiktok", "youtube"]

    filas = con.execute("SELECT red, cuenta_id FROM publicaciones ORDER BY red").fetchall()
    assert [f["red"] for f in filas] == ["tiktok", "youtube"]
    assert all(f["cuenta_id"] for f in filas)            # cada una sabe a qué cuenta va


def test_una_red_que_no_encaja_no_frena_a_las_demas(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    nichos.anadir_cuenta(con, "marketing", "tiktok", etapa="verificada")
    nichos.anadir_cuenta(con, "marketing", "x", etapa="verificada")
    prod = _produccion(con, "reel")                      # un reel no es un hilo

    repartos = publicador.distribuir(con, prod, "marketing")
    ok = [r for r in repartos if r["ok"]]
    fallidos = [r for r in repartos if not r["ok"]]
    assert [r["red"] for r in ok] == ["tiktok"]
    assert fallidos[0]["red"] == "x" and "no encaja" in fallidos[0]["motivo"]


def test_sin_cuentas_listas_se_dice_claramente(entorno):
    _, con = entorno
    nichos.crear(con, "marketing")
    nichos.anadir_cuenta(con, "marketing", "tiktok", etapa="creada")
    prod = _produccion(con)
    with pytest.raises(publicador.ErrorPublicacion, match="no tiene ninguna cuenta"):
        publicador.distribuir(con, prod, "marketing")


def test_la_publicacion_lleva_el_perfil_de_su_nicho(entorno):
    cfg, con = entorno
    nichos.crear(con, "marketing-directo")
    nichos.anadir_cuenta(con, "marketing-directo", "tiktok", etapa="verificada")
    prod = _produccion(con, "reel")
    con.execute("UPDATE producciones SET nicho = 'marketing-directo' WHERE id = ?", (prod,))
    con.commit()

    publicador.distribuir(con, prod, "marketing-directo")
    fila = con.execute("SELECT * FROM publicaciones").fetchone()
    assert publicador._armar(cfg, con, fila).perfil == "MARKETING_DIRECTO"


def test_sin_cuenta_no_hay_perfil(entorno):
    cfg, con = entorno
    prod = _produccion(con)
    pub_id = publicador.programar(con, prod, "tiktok")
    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()
    assert publicador._armar(cfg, con, fila).perfil is None
