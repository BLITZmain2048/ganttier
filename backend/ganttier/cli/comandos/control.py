"""Comandos CTL: control e informes sobre el mismo contrato de la API."""
from __future__ import annotations

import argparse
from datetime import date

from .._base import Context, Result


def _leaf(sub, name, operation, help_text):
    parser = sub.add_parser(name, help=help_text, description=help_text)
    parser.set_defaults(handler=handle, _ctl=operation, command=operation.replace("/", " "))
    return parser


def _body(parser):
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Objeto JSON con campos en español o de la API.")
    group.add_argument("--archivo", help="Archivo JSON; '-' lee stdin.")


def _basis(parser):
    parser.add_argument("--base", choices=["horas", "costo", "actividades"], default="horas")


def _series(parser):
    _basis(parser)
    parser.add_argument("--periodo", choices=["dia", "semana", "mes"], default="semana")
    parser.add_argument("--resumen", action="store_true", help="Conserva extremos, corte, hitos y desviaciones.")
    parser.add_argument("--salida-serie", metavar="ARCHIVO.csv", help="Exporta la serie completa y resume stdout.")


def register(sub: argparse._SubParsersAction) -> None:
    """Registra los grupos control e informes sin cargar el motor."""
    control = sub.add_parser("control", help="Avance, líneas base e indicadores.")
    actions = control.add_subparsers(dest="control_action", required=True)
    for name, help_text in [("avance", "Registros de avance."), ("fecha-corte", "Fecha de corte al fin de jornada."),
                            ("linea-base", "Capturas del programa vigente.")]:
        nested = actions.add_parser(name, help=help_text).add_subparsers(dest="control_operation", required=True)
        if name == "avance":
            _body(_leaf(nested, "registrar", "control/avance/registrar", "Registra un avance."))
            p = _leaf(nested, "lote", "control/avance/lote", "Carga JSON o CSV de forma atómica.")
            p.add_argument("--archivo", required=True)
            p = _leaf(nested, "listar", "control/avance/listar", "Lista el historial de avance.")
            p.add_argument("--limite", type=int)
            p.add_argument("--desde", type=int, default=0)
        elif name == "fecha-corte":
            _leaf(nested, "ver", "control/fecha-corte/ver", "Consulta la fecha de corte.")
            _leaf(nested, "fijar", "control/fecha-corte/fijar", "Fija el corte y sincroniza el avance.").add_argument("fecha", type=date.fromisoformat)
        else:
            p = _leaf(nested, "crear", "control/linea-base/crear", "Captura una línea base.")
            p.add_argument("--nombre", default="Línea base")
            p.add_argument("--sin-activar", action="store_true")
            _leaf(nested, "activar", "control/linea-base/activar", "Activa una línea base del proyecto.").add_argument("id", type=int)
            _leaf(nested, "listar", "control/linea-base/listar", "Lista las líneas base.")
    for name in ("curva-s", "evm", "tablero"):
        _series(_leaf(actions, name, "control/" + name, "Consulta " + name + "."))
    p = _leaf(actions, "evm-por", "control/evm-por", "Agrupa EVM y concilia totales.")
    p.add_argument("agrupacion", choices=["wbs", "instalacion", "habilidad"])
    _basis(p)
    p = _leaf(actions, "pronosticos", "control/pronosticos", "Consulta pronósticos de costo y plazo.")
    _basis(p)
    p.add_argument("--etc", help="Estimación del trabajo pendiente.")
    p = _leaf(actions, "desempeno", "control/desempeno", "Consulta desempeño por trabajador.")
    p.add_argument("--inicio", type=date.fromisoformat)
    p.add_argument("--fin", type=date.fromisoformat)
    p = _leaf(actions, "alertas", "control/alertas", "Evalúa alertas en simulación salvo --escribir.")
    p.add_argument("--resumen", action="store_true")
    p.add_argument("--estado", choices=["nueva", "reconocida", "resuelta"])
    p = _leaf(actions, "calidad", "control/calidad", "Diagnostica calidad sin reprogramar.")
    p.add_argument("--perfil", choices=["proyecto", "mantenimiento"])
    p.add_argument("--incluir-extremos", action="store_true")
    p.add_argument("--inicio-id", type=int, action="append")
    p.add_argument("--fin-id", type=int, action="append")
    for flag, default in [("desfase-largo", 10), ("duracion-larga", 44), ("holgura-alta", 44)]:
        p.add_argument("--" + flag, type=int, default=default)
    p = _leaf(actions, "actividades", "control/actividades", "Consulta una página de actividades.")
    p.add_argument("--pagina", type=int, default=1)
    p.add_argument("--por-pagina", type=int, default=200)
    p.add_argument("--alcance", choices=["todas", "abiertas", "atrasadas"], default="abiertas")
    p.add_argument("--buscar", default="")

    reports = sub.add_parser("informes", help="Generación segura de informes.").add_subparsers(dest="report_action", required=True)
    p = _leaf(reports, "generar", "informes/generar", "Genera informes del período solicitado.")
    p.add_argument("--tipo", choices=["semanal", "mensual", "anual"], required=True)
    p.add_argument("--inicio", type=date.fromisoformat, required=True)
    p.add_argument("--formatos", default="pdf,xlsx,docx,html")
    p.add_argument("--a", help="Carpeta de destino; por defecto data_dir/informes.")
    p = _leaf(reports, "plantel-semanal", "informes/plantel-semanal", "Exporta el plantel semanal nominal.")
    p.add_argument("--inicio", type=date.fromisoformat, required=True)
    p.add_argument("--a", required=True, help="Archivo XLSX de destino.")
    nested = reports.add_parser("automaticos", help="Emisión de períodos cerrados.").add_subparsers(dest="automatic_operation", required=True)
    for name in ("ver", "configurar", "ejecutar"):
        p = _leaf(nested, name, "informes/automaticos/" + name, name.capitalize() + " informes automáticos.")
        if name == "configurar":
            _body(p)


def handle(args: argparse.Namespace, ctx: Context) -> Result:
    """Despacha operaciones con importaciones perezosas y salida JSON normalizada."""
    if args._ctl.startswith("informes/"):
        from ._control_reports import execute
    elif any(args._ctl.startswith("control/" + group + "/") for group in ("avance", "fecha-corte", "linea-base")):
        from ._control_progress import execute
    else:
        from ._control_queries import execute
    return execute(args, ctx)
