"""Vigilante: abre tus paginas de guardados y recoge lo que sea nuevo.

Ninguna de las tres redes avisa cuando guardas algo, asi que esto hace lo que
harias tu: un par de veces al dia abre tu propia lista de guardados, mira los
enlaces y anade a la boveda los que todavia no estaban.

Como esta hecho, y por que asi:

  - Usa TU navegador, con TU sesion ya iniciada. `boveda vigilar --login` abre
    una ventana normal para que entres a mano una vez; la sesion queda en un
    perfil del disco. Aqui nunca se piden ni se guardan contrasenas.
  - Es de solo lectura. Abre la pagina, lee los enlaces y se va. No da likes, no
    sigue a nadie, no comenta, no publica.
  - Va despacio. Por defecto no hace scroll: la lista viene ordenada por lo mas
    reciente, y para una ronda del dia a dia basta con lo de arriba.
  - Saca los enlaces por su forma (/p/, /reel/, /video/, watch?v=) en vez de por
    clases del HTML. Estas paginas se rediseñan cada dos por tres, y una clase
    renombrada tumbaria un raspado por selectores; el formato de una URL de post
    no cambia.

Aviso que conviene tener presente: automatizar el acceso va contra las
condiciones de uso de las tres plataformas, aunque sea tu cuenta y tus datos. El
riesgo real es que te pidan verificacion o te cierren la sesion. Por eso el
vigilante es opcional, va apagado por defecto y el export oficial sigue siendo
la via segura: si un dia esto deja de funcionar, no pierdes nada.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from typing import Any

from . import db
from .config import Config
from .ingest.base import canonizar

# Donde vive la lista de guardados de cada red.
PAGINAS: dict[str, dict[str, Any]] = {
    "instagram": {
        "url": "https://www.instagram.com/{usuario}/saved/",
        "necesita_usuario": True,
        "variable": "BOVEDA_IG_USUARIO",
        "patron": ("/p/", "/reel/"),
    },
    "tiktok": {
        "url": "https://www.tiktok.com/@{usuario}",
        "necesita_usuario": True,
        "variable": "BOVEDA_TIKTOK_USUARIO",
        "pestana": '[data-e2e="favorites-tab"]',   # los favoritos estan en su pestaña
        "patron": ("/video/",),
    },
    "youtube": {
        "url": "https://www.youtube.com/playlist?list=WL",
        "necesita_usuario": False,
        "patron": ("watch?v=",),
    },
}

ESPERA_CARGA = 4000       # ms que se le dan a la pagina para pintar la lista
ESPERA_SCROLL = 1500


class ErrorVigilante(RuntimeError):
    pass


def disponible() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _navegador(cfg: Config, visible: bool):
    if not disponible():
        raise ErrorVigilante(
            'playwright no esta instalado: pip install "boveda[vigilante]" '
            "&& playwright install chromium"
        )
    import os

    from playwright.sync_api import sync_playwright

    play = sync_playwright().start()
    cfg.navegador.mkdir(parents=True, exist_ok=True)
    # Si ya tienes un Chrome o Chromium, se puede usar ese en vez de bajar otro.
    binario = os.environ.get("BOVEDA_CHROMIUM") or None
    contexto = play.chromium.launch_persistent_context(
        str(cfg.navegador), headless=not visible, executable_path=binario,
        viewport={"width": 1280, "height": 900},
        # Un user agent de navegador normal: no se finge nada raro, pero tampoco
        # se anuncia como automatico sin necesidad.
        args=["--disable-blink-features=AutomationControlled"],
    )
    return play, contexto


def entrar(cfg: Config) -> None:
    """Abre una ventana para que inicies sesion a mano, una sola vez."""
    play, contexto = _navegador(cfg, visible=True)
    try:
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
        pagina.goto("https://www.instagram.com/")
        print("Se abrio el navegador. Inicia sesion en las redes que quieras vigilar")
        print("(instagram.com, tiktok.com, youtube.com) y luego cierra la ventana.")
        print("La sesion queda guardada en", cfg.navegador)
        pagina.wait_for_event("close", timeout=0)
    except Exception:  # noqa: BLE001 - cerrar la ventana es la forma de terminar
        pass
    finally:
        contexto.close()
        play.stop()


def _enlaces_de(pagina, patrones: tuple[str, ...]) -> list[str]:
    """Todos los enlaces de la pagina que tengan forma de publicacion."""
    href = pagina.eval_on_selector_all(
        "a[href]", "nodos => nodos.map(n => n.href)")
    vistos: list[str] = []
    for url in href:
        if any(p in url for p in patrones) and url not in vistos:
            vistos.append(url)
    return vistos


def revisar_pagina(cfg: Config, contexto, plataforma: str,
                   profundidad: int | None = None) -> list[str]:
    """Abre la lista de guardados de una red y devuelve los enlaces que ve."""
    ficha = PAGINAS[plataforma]
    usuario = cfg.usuarios.get(plataforma, "")
    if ficha["necesita_usuario"] and not usuario:
        raise ErrorVigilante(
            f"falta tu usuario de {plataforma}: pon "
            f"{ficha.get('variable', 'BOVEDA_' + plataforma.upper() + '_USUARIO')}"
            f" en el .env"
        )

    pagina = contexto.new_page()
    try:
        pagina.goto(ficha["url"].format(usuario=usuario), wait_until="domcontentloaded",
                    timeout=45000)
        pagina.wait_for_timeout(ESPERA_CARGA)

        if ficha.get("pestana"):
            try:
                pagina.click(ficha["pestana"], timeout=8000)
                pagina.wait_for_timeout(ESPERA_CARGA)
            except Exception as exc:  # noqa: BLE001
                raise ErrorVigilante(
                    f"no se encontro la pestaña de favoritos en {plataforma} "
                    f"(¿sesion caducada o rediseño?): {exc}"
                ) from exc

        veces = cfg.profundidad_vigilante if profundidad is None else profundidad
        for _ in range(max(0, veces)):
            pagina.mouse.wheel(0, 4000)
            pagina.wait_for_timeout(ESPERA_SCROLL)

        return _enlaces_de(pagina, tuple(ficha["patron"]))
    finally:
        pagina.close()


def guardar_enlaces(con: sqlite3.Connection, enlaces: list[str],
                    plataforma: str, carpeta: str = "vigilante") -> int:
    """Mete en la boveda los que no estuvieran ya. Devuelve cuantos son nuevos."""
    origen = db.obtener_origen(con, plataforma, carpeta,
                               "recogidos automaticamente de tus guardados")
    nuevos = 0
    for url in enlaces:
        try:
            canonica, detectada, externo, autor = canonizar(url)
        except ValueError:
            continue
        if detectada != plataforma:
            continue
        item_id, era_nuevo = db.insertar_item(con, {
            "origen_id": origen, "plataforma": plataforma, "url_canonica": canonica,
            "id_externo": externo, "autor": autor,
        })
        if era_nuevo:
            nuevos += 1
            db.reindexar(con, item_id)
    con.commit()
    return nuevos


def ronda(cfg: Config, con: sqlite3.Connection, plataformas: list[str] | None = None,
          profundidad: int | None = None, visible: bool = False) -> list[dict[str, Any]]:
    """Una pasada por las plataformas pedidas. Cada una va por su cuenta."""
    plataformas = plataformas or list(PAGINAS)
    play, contexto = _navegador(cfg, visible)
    resultados: list[dict[str, Any]] = []
    try:
        for plataforma in plataformas:
            if plataforma not in PAGINAS:
                resultados.append({"plataforma": plataforma, "error": "red desconocida"})
                continue
            try:
                enlaces = revisar_pagina(cfg, contexto, plataforma, profundidad)
                nuevos = guardar_enlaces(con, enlaces, plataforma)
                aviso = None
                if not enlaces:
                    # Cero enlaces casi nunca significa "no guardaste nada": suele
                    # ser la sesion caducada. Mejor decirlo que callarlo.
                    aviso = ("no se vio ningun enlace: puede que la sesion haya "
                             "caducado. Prueba 'boveda vigilar --login'")
                resultados.append({"plataforma": plataforma, "vistos": len(enlaces),
                                   "nuevos": nuevos, "error": aviso})
            except Exception as exc:  # noqa: BLE001 - una red no tumba la ronda
                resultados.append({"plataforma": plataforma, "vistos": 0, "nuevos": 0,
                                   "error": str(exc)})
    finally:
        contexto.close()
        play.stop()

    con.executemany(
        "INSERT INTO vigilancia (plataforma, vistos, nuevos, error) VALUES (?, ?, ?, ?)",
        [(r["plataforma"], r.get("vistos", 0), r.get("nuevos", 0), r.get("error"))
         for r in resultados],
    )
    con.commit()
    return resultados


def historial(con: sqlite3.Connection, limite: int = 10) -> list[dict[str, Any]]:
    return [dict(f) for f in con.execute(
        "SELECT * FROM vigilancia ORDER BY id DESC LIMIT ?", (limite,))]
