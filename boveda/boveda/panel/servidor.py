"""Servidor del panel: un HTTP local con API JSON y una sola pagina.

Se ata a 127.0.0.1 a proposito. La base tiene tus guardados, tus analisis y tus
credenciales cerca; esto no es un servicio para exponer a la red, es una ventana
local a tu propia boveda.

Sin dependencias: `http.server` de la libreria estandar y una pagina de HTML
plano. Asi el panel abre aunque no tengas nada mas instalado.
"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import db, montaje, nichos, publicador
from ..config import Config
from . import consultas

PAGINA = Path(__file__).resolve().parent / "index.html"


class Panel(BaseHTTPRequestHandler):
    cfg: Config

    # --- utilidades ---------------------------------------------------------

    def log_message(self, formato: str, *args: Any) -> None:
        pass  # el panel no ensucia la terminal con una linea por peticion

    def _conexion(self) -> sqlite3.Connection:
        # Una conexion por peticion: el servidor es multihilo y sqlite no
        # comparte conexiones entre hilos.
        con = db.conectar(self.cfg.db)
        db.inicializar(con)
        return con

    def _json(self, datos: Any, codigo: int = 200) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _archivo(self, ruta: Path) -> None:
        datos = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         mimetypes.guess_type(ruta.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _error(self, mensaje: str, codigo: int = 404) -> None:
        self._json({"error": mensaje}, codigo)

    # --- rutas --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - lo impone http.server
        partes = urlparse(self.path)
        ruta = partes.path
        consulta = {k: v[0] for k, v in parse_qs(partes.query).items()}

        if ruta in ("/", "/index.html"):
            return self._archivo(PAGINA)

        if ruta == "/api/tablero":
            con = self._conexion()
            try:
                datos = consultas.tablero(con, consulta)
                datos["resumen"] = consultas.resumen(con)
                return self._json(datos)
            finally:
                con.close()

        if ruta == "/api/nichos":
            con = self._conexion()
            try:
                return self._json({"nichos": nichos.listar(con),
                                   "etapas": nichos.ETAPAS})
            finally:
                con.close()

        if ruta.startswith("/api/nicho/"):
            con = self._conexion()
            try:
                return self._json(consultas.tablero_nicho(con, ruta.rsplit("/", 1)[-1]))
            except nichos.ErrorNicho as exc:
                return self._error(str(exc))
            finally:
                con.close()

        if ruta.startswith("/api/item/"):
            con = self._conexion()
            try:
                detalle = consultas.detalle(con, int(ruta.rsplit("/", 1)[-1]))
                return self._json(detalle) if detalle else self._error("no existe ese item")
            finally:
                con.close()

        if ruta.startswith("/miniatura/"):
            con = self._conexion()
            try:
                fila = con.execute(
                    "SELECT ruta FROM fotogramas WHERE item_id = ? ORDER BY indice LIMIT 1",
                    (int(ruta.rsplit("/", 1)[-1]),),
                ).fetchone()
            finally:
                con.close()
            # Solo se sirve lo que la propia base apunta: nada de rutas del cliente.
            if fila and Path(fila["ruta"]).is_file():
                return self._archivo(Path(fila["ruta"]))
            return self._error("sin miniatura")

        if ruta.startswith("/video/"):
            con = self._conexion()
            try:
                fila = con.execute(
                    "SELECT ruta_video FROM montajes WHERE produccion_id = ?",
                    (int(ruta.rsplit("/", 1)[-1]),),
                ).fetchone()
            finally:
                con.close()
            if fila and Path(fila["ruta_video"]).is_file():
                return self._archivo(Path(fila["ruta_video"]))
            return self._error("sin video montado")

        return self._error("ruta desconocida")

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/accion":
            return self._error("ruta desconocida")
        largo = int(self.headers.get("Content-Length") or 0)
        try:
            peticion = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError:
            return self._error("json invalido", 400)

        con = self._conexion()
        try:
            return self._json(self._ejecutar(con, peticion))
        except Exception as exc:  # noqa: BLE001 - el panel informa, no revienta
            return self._error(f"{type(exc).__name__}: {exc}", 400)
        finally:
            con.close()

    def _ejecutar(self, con: sqlite3.Connection, peticion: dict[str, Any]) -> dict[str, Any]:
        """Solo acciones reversibles.

        Publicar no esta aqui a proposito: es lo unico que no se puede deshacer,
        y un clic de mas en un panel no deberia sacar algo a tus redes. El panel
        te da el comando exacto y tu lo confirmas en la terminal.
        """
        accion = peticion.get("accion")
        identificador = int(peticion.get("id") or 0)

        if accion == "aprobar":
            publicador.aprobar(con, identificador)
            return {"ok": True, "mensaje": f"produccion {identificador} aprobada"}

        if accion == "devolver":
            publicador.aprobar(con, identificador, "borrador")
            return {"ok": True, "mensaje": f"produccion {identificador} vuelta a borrador"}

        if accion == "cancelar":
            publicador.cancelar(con, identificador)
            return {"ok": True, "mensaje": f"publicacion {identificador} cancelada"}

        if accion == "crear_nicho":
            clave = (peticion.get("clave") or "").strip()
            nichos.crear(con, clave, peticion.get("nombre"), peticion.get("descripcion"))
            return {"ok": True, "mensaje": f"nicho '{nichos.normalizar_clave(clave)}' creado",
                    "clave": nichos.normalizar_clave(clave)}

        if accion == "tarea":
            nichos.marcar_tarea(con, identificador, bool(peticion.get("hecha", True)))
            return {"ok": True, "mensaje": f"tarea {identificador} actualizada"}

        if accion == "cuenta_nueva":
            cuenta_id = nichos.anadir_cuenta(
                con, peticion.get("nicho") or "", peticion.get("red") or "",
                peticion.get("handle"), peticion.get("url"), peticion.get("estrategia"),
                peticion.get("etapa") or "creada")
            return {"ok": True, "mensaje": f"cuenta #{cuenta_id} guardada"}

        if accion == "cuenta_etapa":
            nichos.mover_cuenta(con, identificador, peticion.get("etapa") or "creada")
            return {"ok": True, "mensaje": f"cuenta {identificador} movida"}

        if accion == "cuenta_borrar":
            nichos.borrar_cuenta(con, identificador)
            return {"ok": True, "mensaje": f"cuenta {identificador} borrada"}

        if accion == "reintentar":
            destinos = {"error_descarga": "importado",
                        "error_transcripcion": "descargado",
                        "error_analisis": "transcrito"}
            fila = con.execute("SELECT estado FROM items WHERE id = ?",
                               (identificador,)).fetchone()
            if fila is None or fila["estado"] not in destinos:
                raise LookupError("ese item no esta en error")
            db.marcar(con, identificador, destinos[fila["estado"]], None)
            return {"ok": True, "mensaje": f"item {identificador} devuelto a la cola"}

        raise ValueError(f"accion desconocida: {accion}")


def arrancar(cfg: Config, puerto: int = 8765, abrir: bool = True) -> None:
    Panel.cfg = cfg
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), Panel)
    direccion = f"http://127.0.0.1:{puerto}"
    print(f"Panel en {direccion}   (Ctrl+C para parar)")
    if abrir:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(direccion)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel cerrado.")
    finally:
        servidor.server_close()
