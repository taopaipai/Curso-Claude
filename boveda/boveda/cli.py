"""Interfaz de linea de comandos de la boveda.

Flujo tipico:
    boveda init
    boveda importar instagram ~/export/saved_posts.json
    boveda descargar --limite 50
    boveda transcribir
    boveda ocr              # texto en pantalla de los videos sin voz
    boveda analizar
    boveda buscar "hook de curiosidad"
    boveda producir 42 --formato carrusel --nicho finanzas
    boveda aprobar 7
    boveda montar 7 --fondo fondo.jpg       # voz + subtitulos + video
    boveda publicar 7 --red instagram --confirmar
    boveda exportar --formato md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import dataclasses

from . import config, db, export, montaje, publicador
from .ingest import IMPORTADORES
from .publish import FORMATOS, REDES
from .pipeline import analyze, download, ocr, repurpose, transcribe

ETAPAS = {
    "descargar": ("importado", download.procesar),
    "transcribir": ("descargado", transcribe.procesar),
    "analizar": ("transcrito", analyze.procesar),
}


def _abrir(args) -> tuple[config.Config, sqlite3.Connection]:
    cfg = config.cargar(args.home)
    cfg.preparar_directorios()
    con = db.conectar(cfg.db)
    db.inicializar(con)
    return cfg, con


def cmd_init(args) -> int:
    cfg, con = _abrir(args)
    con.close()
    print(f"Boveda lista en {cfg.home}")
    print(f"  base de datos: {cfg.db}")
    print(f"  audio:         {cfg.audio}")
    print(f"  media:         {cfg.media}")
    return 0


def cmd_importar(args) -> int:
    cfg, con = _abrir(args)
    importador = IMPORTADORES[args.plataforma]
    origen = args.origen if args.plataforma == "youtube" else Path(args.origen)
    registros = importador(origen, args.carpeta)

    nuevos = repetidos = 0
    for registro in registros:
        carpeta = registro.pop("carpeta", None)
        registro["origen_id"] = db.obtener_origen(con, registro["plataforma"], carpeta)
        item_id, era_nuevo = db.insertar_item(con, registro)
        if era_nuevo:
            nuevos += 1
            db.reindexar(con, item_id)
        else:
            repetidos += 1
    con.commit()
    con.close()
    print(f"Importados {len(registros)} registros: {nuevos} nuevos, {repetidos} ya estaban.")
    return 0


def cmd_etapa(args) -> int:
    cfg, con = _abrir(args)
    estado_origen, procesar = ETAPAS[args.comando]

    if args.comando == "descargar":
        try:
            download.comprobar_dependencias()
        except download.HerramientaFaltante as exc:
            print(exc, file=sys.stderr)
            return 2

    cliente = analyze.cliente() if args.comando == "analizar" else None
    pendientes = db.pendientes(con, estado_origen, args.limite, args.plataforma)
    if not pendientes:
        print(f"No hay items en estado '{estado_origen}'.")
        con.close()
        return 0

    fallos = 0
    for i, item in enumerate(pendientes, 1):
        etiqueta = (item["titulo"] or item["url_canonica"])[:70]
        print(f"[{i}/{len(pendientes)}] #{item['id']} {etiqueta}", flush=True)
        if cliente is not None:
            procesar(cfg, con, item, cliente)
        else:
            procesar(cfg, con, item)
        estado = con.execute("SELECT estado, error FROM items WHERE id = ?",
                             (item["id"],)).fetchone()
        if estado["estado"].startswith("error"):
            fallos += 1
            print(f"    ! {estado['error']}", file=sys.stderr)
    con.close()
    print(f"Listo: {len(pendientes) - fallos} ok, {fallos} con error.")
    return 1 if fallos == len(pendientes) else 0


def cmd_ocr(args) -> int:
    """Lee el texto quemado en pantalla. No es una etapa obligatoria del flujo:
    trabaja sobre los items ya descargados que no tienen (apenas) voz."""
    cfg, con = _abrir(args)
    try:
        ocr.comprobar_dependencias()
    except download.HerramientaFaltante as exc:
        print(exc, file=sys.stderr)
        return 2

    pendientes = ocr.candidatos(con, args.umbral if args.umbral is not None else cfg.umbral_ocr,
                                args.limite, args.todos, args.plataforma)
    if not pendientes:
        print("No hay items sin voz pendientes de OCR. Usa --todos para forzar.")
        con.close()
        return 0

    cliente = analyze.cliente() if cfg.motor_ocr != "tesseract" else None
    fallos = 0
    for i, item in enumerate(pendientes, 1):
        etiqueta = (item["titulo"] or item["url_canonica"])[:70]
        print(f"[{i}/{len(pendientes)}] #{item['id']} {etiqueta}", flush=True)
        try:
            ocr.procesar(cfg, con, item, cliente)
        except Exception as exc:  # noqa: BLE001
            fallos += 1
            print(f"    ! {exc}", file=sys.stderr)
            continue
        leido = con.execute("SELECT texto, n_fotogramas FROM ocr WHERE item_id = ?",
                            (item["id"],)).fetchone()
        print(f"    {leido['n_fotogramas']} fotogramas, "
              f"{len(leido['texto'])} caracteres leidos")
    con.close()
    print(f"Listo: {len(pendientes) - fallos} ok, {fallos} con error.")
    return 1 if fallos == len(pendientes) else 0


def cmd_fotogramas(args) -> int:
    cfg, con = _abrir(args)
    filas = con.execute(
        "SELECT indice, segundo, texto, descripcion, ruta FROM fotogramas "
        "WHERE item_id = ? ORDER BY indice", (args.item_id,)
    ).fetchall()
    if not filas:
        print(f"El item {args.item_id} no tiene OCR todavia.", file=sys.stderr)
        return 1
    for fila in filas:
        print(f"[{fila['indice']:>2}] {fila['segundo'] or 0:>6.1f}s  {fila['texto'] or ''}")
        if fila["descripcion"]:
            print(f"      visual: {fila['descripcion']}")
        if args.rutas:
            print(f"      {fila['ruta']}")
    con.close()
    return 0


def cmd_reintentar(args) -> int:
    cfg, con = _abrir(args)
    destino = {"error_descarga": "importado", "error_transcripcion": "descargado",
               "error_analisis": "transcrito"}
    total = 0
    for estado_error, estado_previo in destino.items():
        cur = con.execute(
            "UPDATE items SET estado = ?, error = NULL WHERE estado = ?",
            (estado_previo, estado_error),
        )
        total += cur.rowcount
    con.commit()
    con.close()
    print(f"{total} items devueltos a la cola.")
    return 0


def cmd_buscar(args) -> int:
    cfg, con = _abrir(args)
    filas = db.buscar(con, args.consulta, args.limite)
    if not filas:
        print("Sin resultados.")
    for fila in filas:
        print(f"#{fila['item_id']} [{fila['plataforma']}] {fila['titulo'] or '(sin titulo)'}")
        print(f"    {fila['autor'] or '?'} · {fila['tipo_contenido'] or '-'} · "
              f"{fila['nicho'] or '-'} · vigencia: {fila['vigencia_estado'] or '-'}")
        print(f"    {fila['extracto']}")
        print(f"    {fila['url_canonica']}")
    con.close()
    return 0


def cmd_mostrar(args) -> int:
    cfg, con = _abrir(args)
    fila = con.execute(
        """
        SELECT i.*, a.analisis_json, t.texto AS transcripcion
        FROM items i
        LEFT JOIN analisis a        ON a.item_id = i.id
        LEFT JOIN transcripciones t ON t.item_id = i.id
        WHERE i.id = ?
        """,
        (args.item_id,),
    ).fetchone()
    if fila is None:
        print(f"No existe el item {args.item_id}", file=sys.stderr)
        return 1
    registro = dict(fila)
    if registro.get("analisis_json"):
        registro["analisis"] = json.loads(registro.pop("analisis_json"))
    print(json.dumps(registro, ensure_ascii=False, indent=2))
    con.close()
    return 0


def cmd_producir(args) -> int:
    cfg, con = _abrir(args)
    try:
        prod_id = repurpose.generar(cfg, con, args.item_id, args.formato,
                                    args.nicho, args.notas)
    except (LookupError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    fila = con.execute("SELECT cuerpo FROM producciones WHERE id = ?", (prod_id,)).fetchone()
    print(fila["cuerpo"])
    print(f"\n-- guardado como produccion #{prod_id}", file=sys.stderr)
    con.close()
    return 0


def cmd_montar(args) -> int:
    cfg, con = _abrir(args)
    if args.sin_voz:
        cfg = dataclasses.replace(cfg, motor_voz="ninguna")
    if args.sin_karaoke:
        cfg = dataclasses.replace(cfg, karaoke="no")

    for nombre, ruta in (("fondo", args.fondo), ("musica", args.musica)):
        if ruta and not Path(ruta).is_file():
            print(f"No existe el archivo de {nombre}: {ruta}", file=sys.stderr)
            return 1
    try:
        resumen = montaje.montar(
            cfg, con, args.produccion_id,
            fondo=Path(args.fondo) if args.fondo else None,
            musica=Path(args.musica) if args.musica else None,
            rehacer=args.rehacer,
        )
    except (montaje.ErrorMontaje, download.HerramientaFaltante) as exc:
        print(exc, file=sys.stderr)
        return 1

    subtitulos = ("karaoke, palabra por palabra" if resumen["karaoke"]
                  else "por bloques (sin alineacion)")
    print(f"Video montado: {resumen['video']}")
    print(f"  {len(resumen['escenas'])} escenas, {resumen['duracion']:.1f} s, "
          f"subtitulos {subtitulos}")
    for aviso in resumen["avisos"]:
        print(f"  ! {aviso}", file=sys.stderr)
    for i, escena in enumerate(resumen["escenas"], 1):
        rotulo = escena.get("rotulo") or ""
        print(f"  [{i:>2}] {escena.get('duracion', 0):>5.1f}s  {rotulo[:38]:<38} "
              f"{escena.get('voz', '')[:44]}")
    print("\nYa puedes publicarlo: el video se usa solo si no pasas --media.")
    con.close()
    return 0


def cmd_aprobar(args) -> int:
    cfg, con = _abrir(args)
    try:
        publicador.aprobar(con, args.produccion_id,
                           "borrador" if args.deshacer else "aprobado")
    except publicador.ErrorPublicacion as exc:
        print(exc, file=sys.stderr)
        return 1
    estado = "devuelta a borrador" if args.deshacer else "aprobada para publicar"
    print(f"Produccion #{args.produccion_id} {estado}.")
    con.close()
    return 0


def cmd_redes(args) -> int:
    """Dice que redes estan configuradas y con que cuenta, sin publicar nada."""
    cfg, con = _abrir(args)
    con.close()
    for nombre in sorted(REDES):
        modulo = REDES[nombre]
        formatos = ", ".join(sorted(FORMATOS.get(nombre, set())))
        if not modulo.configurada(cfg):
            print(f"  {nombre:<10} sin credenciales      ({formatos})")
            continue
        if not args.verificar:
            print(f"  {nombre:<10} configurada           ({formatos})")
            continue
        try:
            print(f"  {nombre:<10} {modulo.verificar(cfg):<21} ({formatos})")
        except Exception as exc:  # noqa: BLE001
            print(f"  {nombre:<10} ERROR: {exc}", file=sys.stderr)
    if not args.verificar:
        print("\nUsa --verificar para comprobar los tokens contra cada API.")
    return 0


def _publicar_filas(cfg, con, filas, ensayo: bool) -> int:
    fallos = 0
    for fila in filas:
        cabecera = (f"#{fila['id']} produccion {fila['produccion_id']} -> {fila['red']}"
                    + (f" (programada {fila['programado_para']})" if fila["programado_para"] else ""))
        print(cabecera, flush=True)
        try:
            resultado = publicador.ejecutar(cfg, con, fila, ensayo=ensayo)
        except Exception as exc:  # noqa: BLE001
            fallos += 1
            print(f"    ! {exc}", file=sys.stderr)
            continue
        detalle = resultado.detalle or "ok"
        print(f"    {detalle}" + (f"  {resultado.url_remota}" if resultado.url_remota else ""))
    return fallos


def cmd_publicar(args) -> int:
    """Programa y publica en un solo paso. Sin --confirmar es un ensayo."""
    cfg, con = _abrir(args)
    try:
        pub_id = publicador.programar(
            con, args.produccion_id, args.red, cuando=args.cuando,
            media=args.media, media_url=args.media_url, forzar=args.forzar)
    except publicador.ErrorPublicacion as exc:
        print(exc, file=sys.stderr)
        return 1

    fila = con.execute("SELECT * FROM publicaciones WHERE id = ?", (pub_id,)).fetchone()
    if args.cuando:
        print(f"Programada como publicacion #{pub_id} para {args.cuando}. "
              f"Se enviara al ejecutar 'boveda cola --confirmar'.")
        con.close()
        return 0

    fallos = _publicar_filas(cfg, con, [fila], ensayo=not args.confirmar)
    if not args.confirmar:
        print("\nEsto fue un ensayo: no se envio nada. Repite con --confirmar para publicar "
              "de verdad.")
        publicador.cancelar(con, pub_id)
    con.close()
    return 1 if fallos else 0


def cmd_cola(args) -> int:
    cfg, con = _abrir(args)
    filas = publicador.pendientes(con, args.limite, args.red, args.todas)
    if not filas:
        print("No hay publicaciones pendientes.")
        con.close()
        return 0
    fallos = _publicar_filas(cfg, con, filas, ensayo=not args.confirmar)
    if not args.confirmar:
        print("\nEsto fue un ensayo: la cola sigue intacta. Repite con --confirmar.")
    con.close()
    return 1 if fallos == len(filas) else 0


def cmd_publicaciones(args) -> int:
    cfg, con = _abrir(args)
    filas = con.execute(
        """
        SELECT pu.*, pr.formato, pr.titulo
        FROM publicaciones pu JOIN producciones pr ON pr.id = pu.produccion_id
        ORDER BY pu.id DESC LIMIT ?
        """,
        (args.limite,),
    ).fetchall()
    if not filas:
        print("Todavia no hay publicaciones registradas.")
    for fila in filas:
        cuando = fila["publicado_en"] or fila["programado_para"] or fila["creado_en"]
        print(f"#{fila['id']:<4} {fila['estado']:<11} {fila['red']:<10} "
              f"{fila['formato']:<10} {cuando}")
        print(f"      produccion {fila['produccion_id']}: {(fila['titulo'] or '')[:60]}")
        if fila["url_remota"]:
            print(f"      {fila['url_remota']}")
        if fila["error"]:
            print(f"      ! {fila['error'][:200]}")
    con.close()
    return 0


def cmd_cancelar(args) -> int:
    cfg, con = _abrir(args)
    try:
        publicador.cancelar(con, args.publicacion_id)
    except publicador.ErrorPublicacion as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Publicacion #{args.publicacion_id} cancelada.")
    con.close()
    return 0


def cmd_exportar(args) -> int:
    cfg, con = _abrir(args)
    destino = Path(args.destino) if args.destino else cfg.exports
    if args.formato == "md":
        total = export.a_markdown(con, destino)
        print(f"{total} notas escritas en {destino}")
    else:
        salida = destino if destino.suffix == ".json" else destino / "boveda.json"
        total = export.a_json(con, salida)
        print(f"{total} items escritos en {salida}")
    con.close()
    return 0


def cmd_estado(args) -> int:
    cfg, con = _abrir(args)
    print("Por estado:")
    for fila in con.execute(
        "SELECT estado, COUNT(*) n FROM items GROUP BY estado ORDER BY n DESC"
    ):
        print(f"  {fila['estado']:<22} {fila['n']}")
    print("\nPor plataforma:")
    for fila in con.execute(
        "SELECT plataforma, COUNT(*) n FROM items GROUP BY plataforma ORDER BY n DESC"
    ):
        print(f"  {fila['plataforma']:<22} {fila['n']}")
    mudos = con.execute(
        "SELECT COUNT(*) n FROM items i LEFT JOIN transcripciones t ON t.item_id = i.id "
        "LEFT JOIN ocr o ON o.item_id = i.id "
        "WHERE i.estado IN ('descargado','transcrito') AND o.item_id IS NULL "
        "AND LENGTH(COALESCE(t.texto,'')) < 200"
    ).fetchone()["n"]
    con_ocr = con.execute("SELECT COUNT(*) n FROM ocr").fetchone()["n"]
    print(f"\nCon texto en pantalla leido: {con_ocr}")
    if mudos:
        print(f"Sin voz y pendientes de OCR:  {mudos}  (ejecuta: boveda ocr)")

    print("\nPor vigencia (ya analizados):")
    for fila in con.execute(
        "SELECT vigencia_estado, COUNT(*) n, ROUND(AVG(valor_historico), 1) v "
        "FROM analisis GROUP BY vigencia_estado ORDER BY n DESC"
    ):
        print(f"  {fila['vigencia_estado'] or '-':<22} {fila['n']}  (valor historico medio {fila['v']})")
    print("\nProducciones:")
    for fila in con.execute(
        "SELECT estado, COUNT(*) n FROM producciones GROUP BY estado ORDER BY n DESC"
    ):
        print(f"  {fila['estado']:<22} {fila['n']}")

    filas = con.execute(
        "SELECT estado, COUNT(*) n FROM publicaciones GROUP BY estado ORDER BY n DESC"
    ).fetchall()
    if filas:
        print("\nPublicaciones:")
        for fila in filas:
            print(f"  {fila['estado']:<22} {fila['n']}")
    con.close()
    return 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="boveda", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--home", help="carpeta de datos (por defecto BOVEDA_HOME o ./data)")
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("init", help="crea la base de datos y las carpetas").set_defaults(func=cmd_init)

    imp = sub.add_parser("importar", help="importa un export de guardados")
    imp.add_argument("plataforma", choices=sorted(IMPORTADORES))
    imp.add_argument("origen", help="archivo del export (o URL de playlist en youtube)")
    imp.add_argument("--carpeta", help="nombre de coleccion para estos guardados")
    imp.set_defaults(func=cmd_importar)

    for nombre, ayuda in (
        ("descargar", "baja metadatos y audio de los items importados"),
        ("transcribir", "transcribe el audio descargado"),
        ("analizar", "analiza con Claude los items transcritos"),
    ):
        sp = sub.add_parser(nombre, help=ayuda)
        sp.add_argument("--limite", type=int, help="procesa como mucho N items")
        sp.add_argument("--plataforma", help="filtra por plataforma")
        sp.set_defaults(func=cmd_etapa)

    ocr_p = sub.add_parser("ocr", help="lee el texto en pantalla de los videos sin voz")
    ocr_p.add_argument("--limite", type=int, help="procesa como mucho N items")
    ocr_p.add_argument("--plataforma", help="filtra por plataforma")
    ocr_p.add_argument("--todos", action="store_true",
                       help="tambien los que ya tienen voz transcrita")
    ocr_p.add_argument("--umbral", type=int,
                       help="hace OCR si la transcripcion tiene menos de N caracteres")
    ocr_p.set_defaults(func=cmd_ocr)

    fot = sub.add_parser("fotogramas", help="muestra lo leido fotograma a fotograma")
    fot.add_argument("item_id", type=int)
    fot.add_argument("--rutas", action="store_true", help="incluye la ruta de cada imagen")
    fot.set_defaults(func=cmd_fotogramas)

    sub.add_parser("reintentar", help="devuelve a la cola los items con error").set_defaults(
        func=cmd_reintentar)

    bus = sub.add_parser("buscar", help="busqueda full-text sobre transcripciones y analisis")
    bus.add_argument("consulta")
    bus.add_argument("--limite", type=int, default=20)
    bus.set_defaults(func=cmd_buscar)

    mos = sub.add_parser("mostrar", help="vuelca un item completo en JSON")
    mos.add_argument("item_id", type=int)
    mos.set_defaults(func=cmd_mostrar)

    pro = sub.add_parser("producir", help="genera contenido nuestro a partir de un item")
    pro.add_argument("item_id", type=int)
    pro.add_argument("--formato", required=True, choices=sorted(repurpose.INSTRUCCIONES))
    pro.add_argument("--nicho")
    pro.add_argument("--notas", help="indicaciones extra para el guionista")
    pro.set_defaults(func=cmd_producir)

    mon = sub.add_parser("montar", help="arma el video: voz, subtitulos y fondo")
    mon.add_argument("produccion_id", type=int)
    mon.add_argument("--fondo", help="imagen o video de fondo (por defecto, color plano)")
    mon.add_argument("--musica", help="pista de fondo, se mezcla baja")
    mon.add_argument("--sin-voz", dest="sin_voz", action="store_true",
                     help="solo rotulos y subtitulos, sin sintetizar voz")
    mon.add_argument("--sin-karaoke", dest="sin_karaoke", action="store_true",
                     help="subtitulos por bloques en vez de palabra por palabra")
    mon.add_argument("--rehacer", action="store_true",
                     help="vuelve a desglosar el guion en vez de reusar el desglose")
    mon.set_defaults(func=cmd_montar)

    apr = sub.add_parser("aprobar", help="marca una produccion como lista para publicar")
    apr.add_argument("produccion_id", type=int)
    apr.add_argument("--deshacer", action="store_true", help="la devuelve a borrador")
    apr.set_defaults(func=cmd_aprobar)

    red = sub.add_parser("redes", help="que redes estan configuradas")
    red.add_argument("--verificar", action="store_true",
                     help="comprueba los tokens llamando a cada API")
    red.set_defaults(func=cmd_redes)

    pub = sub.add_parser("publicar", help="publica una produccion aprobada en una red")
    pub.add_argument("produccion_id", type=int)
    pub.add_argument("--red", required=True, choices=sorted(REDES))
    pub.add_argument("--media", help="archivo de video o imagen que se sube")
    pub.add_argument("--media-url", dest="media_url",
                     help="URL publica del medio (Instagram la exige)")
    pub.add_argument("--cuando", help="fecha ISO para dejarla programada en la cola")
    pub.add_argument("--forzar", action="store_true",
                     help="salta las comprobaciones de estado y de formato")
    pub.add_argument("--confirmar", action="store_true",
                     help="publica de verdad; sin esto es un ensayo")
    pub.set_defaults(func=cmd_publicar)

    cola = sub.add_parser("cola", help="procesa las publicaciones programadas que ya tocan")
    cola.add_argument("--red", choices=sorted(REDES))
    cola.add_argument("--limite", type=int)
    cola.add_argument("--todas", action="store_true",
                      help="incluye las programadas para mas adelante")
    cola.add_argument("--confirmar", action="store_true",
                      help="publica de verdad; sin esto es un ensayo")
    cola.set_defaults(func=cmd_cola)

    lis = sub.add_parser("publicaciones", help="historial de publicaciones")
    lis.add_argument("--limite", type=int, default=20)
    lis.set_defaults(func=cmd_publicaciones)

    can = sub.add_parser("cancelar", help="cancela una publicacion programada")
    can.add_argument("publicacion_id", type=int)
    can.set_defaults(func=cmd_cancelar)

    exp = sub.add_parser("exportar", help="vuelca la boveda a markdown o json")
    exp.add_argument("--formato", choices=("md", "json"), default="md")
    exp.add_argument("--destino")
    exp.set_defaults(func=cmd_exportar)

    sub.add_parser("estado", help="resumen de la boveda").set_defaults(func=cmd_estado)
    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
