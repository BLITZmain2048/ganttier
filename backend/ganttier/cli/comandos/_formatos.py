"""Catálogo de archivos y carga adicional en proyectos existentes."""
from __future__ import annotations

import argparse
import hashlib
import shutil
from contextlib import closing
from pathlib import Path
from typing import Any

from .._base import CliError, Context, Result

ROOT = Path(__file__).resolve().parents[4]
DESCRIPTIONS = {
    'om_wbs': 'Matriz de mantenimiento OM-WBS.',
    'e2_mp': 'Formulario económico E-2-MP.',
    'caso_json': 'Caso de proyecto en JSON.',
    'inventario_equipos': 'Inventario de equipos en Excel o PDF.',
    'msp_excel': 'Exportación tabular de MS Project, español o inglés.',
    'p6_excel': 'Exportación de Primavera P6, hoja única o multitabla.',
    'carta_gantt': 'Matriz de actividades por meses o semanas.',
    'pauta_equipos': 'Pauta de mantenimiento por equipo o tipo de equipo.',
    'personal': 'Personal, turnos, ausencias y equipos de un proyecto existente.',
    'avance': 'Avance físico y HH reales de un proyecto existente.',
}
ASSETS = {
    'msp_excel': ('plantilla_msp_*.xlsx', 'se220_msp_*'),
    'p6_excel': ('plantilla_p6_*.xlsx', 'se220_p6_*'),
    'carta_gantt': ('plantilla_carta*.xlsx', 'carta_*.xlsx'),
    'pauta_equipos': ('plantilla_pauta_*.xlsx', 'pauta_*.xlsx'),
    'personal': ('personal.xlsx', 'personal_*.xlsx'),
    'avance': ('avance.xlsx', 'avance_*.xlsx'),
}
DEFAULT_TEMPLATE = {'msp_excel': 'plantilla_msp_es.xlsx', 'p6_excel': 'plantilla_p6_unica.xlsx',
                    'pauta_equipos': 'plantilla_pauta_A.xlsx'}


def catalog() -> list[dict]:
    """Enumera lectores instalados y sus archivos distribuidos, relativos a app/."""
    from ...importadores import servicio
    from ...importadores.proyecto_externo import AdaptadorMSP, AdaptadorP6
    from ...importadores.carta_gantt import AdaptadorCartaGantt
    from ...importadores.pauta_equipos import AdaptadorPauta
    formats = {r['formato']: r['extensiones'] for r in servicio.formatos()}
    for reader in (AdaptadorMSP, AdaptadorP6, AdaptadorCartaGantt, AdaptadorPauta):
        formats[reader.formato] = sorted(reader.extensiones)
    formats.update(personal=['.xlsx'], avance=['.xlsx'])
    rows = []
    for name, extensions in sorted(formats.items()):
        templates, examples = [], []
        if name in ASSETS:
            for folder, pattern, target in (('plantillas', ASSETS[name][0], templates),
                                             ('ejemplos', ASSETS[name][1], examples)):
                target.extend(p.relative_to(ROOT).as_posix() for p in sorted((ROOT / folder / 'formatos').glob(pattern)) if p.is_file())
        rows.append(dict(format=name, extensions=extensions,
                         description=DESCRIPTIONS.get(name, f'Importador asistido {name}.'),
                         templates=templates, examples=examples,
                         existing_project=name in ('personal', 'avance')))
    return rows


def formats_handler(args: Any, ctx: Context) -> Result:
    """Lista formatos o copia una plantilla mediante creación exclusiva."""
    rows = catalog()
    if args.action == 'listar':
        return Result({'formats': rows, 'asset_root': str(ROOT)})
    row = next((r for r in rows if r['format'] == args.format), None)
    if row is None:
        raise CliError('uso', f'Formato desconocido: {args.format}.', hint='Use formatos listar.')
    if not row['templates']:
        raise CliError('no_encontrado', f'No hay plantilla distribuida para {args.format}.')
    source = ROOT / row['templates'][0]
    preferred = DEFAULT_TEMPLATE.get(args.format)
    if preferred:
        source = ROOT / 'plantillas' / 'formatos' / preferred
    destination = Path(args.destino).expanduser()
    if destination.is_dir():
        destination /= source.name
    try:
        with source.open('rb') as src, destination.open('xb') as dst:
            shutil.copyfileobj(src, dst)
    except FileExistsError as exc:
        raise CliError('datos_invalidos', f'El destino ya existe: {destination}.') from exc
    return Result({'format': args.format, 'path': str(destination.resolve()),
                   'source': str(source), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})


class _DeferredCommit:
    """Delega SQLite, reservando la confirmación final a Context.mutation.

    El dominio exige comenzar sin transacción y abre BEGIN IMMEDIATE antes de
    aplicar. Su commit se difiere; rollback y SAVEPOINT conservan su semántica.
    No se sustituye ni se modifica globalmente ninguna función del dominio.
    """

    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        """Context.mutation confirma la conexión real al terminar el handler."""

def import_extra_handler(args: Any, ctx: Context) -> Result:
    """Ejecuta el importador de dominio dentro del espacio seguro de la CLI."""
    from ...excel_io.personal_avance import import_workbook
    source = Path(args.source).expanduser()
    if not source.is_file():
        raise CliError('no_encontrado', f'No existe el archivo: {source}.')
    # Resolver antes de crear respaldos; nunca crear implícitamente proyectos.
    with closing(ctx.conn()) as conn:
        ctx.require_project(conn)
    with ctx.mutation(f'CLI importar {args.kind}') as conn:
        pid = ctx.require_project(conn)
        data = import_workbook(_DeferredCommit(conn), pid, source, args.kind, dry_run=False,
                               modo=getattr(args, 'modo', 'merge'))
    data['project_id'] = pid
    data['dry_run'] = not ctx.write
    data['applied'] = bool(ctx.write and data['applied'])
    if not ctx.write:
        data['backup'] = None  # el respaldo de dominio pertenecía a la copia descartada
    return Result(data, exit_code=2 if data['errors'] else 0)


def register(sub: argparse._SubParsersAction, imports: argparse._SubParsersAction) -> None:
    """Añade formatos y las dos importaciones sin modificar los otros grupos."""
    group = sub.add_parser('formatos', help='Formatos de entrada, plantillas y ejemplos disponibles.')
    actions = group.add_subparsers(dest='action', required=True)
    actions.add_parser('listar', help='Listar extensiones, descripciones y archivos asociados.').set_defaults(handler=formats_handler)
    parser = actions.add_parser('plantilla', help='Copiar una plantilla sin sobrescribir archivos.')
    parser.add_argument('format', metavar='FORMATO')
    parser.add_argument('--destino', required=True, help='Archivo nuevo o directorio existente.')
    parser.set_defaults(handler=formats_handler)
    for kind in ('personal', 'avance'):
        parser = imports.add_parser(kind, help=f'Importar {kind} al proyecto de -p; simula salvo --escribir.')
        parser.add_argument('source', metavar='ARCHIVO')
        if kind == 'personal':
            parser.add_argument('--modo', choices=['merge', 'replace'], default='merge')
        parser.set_defaults(handler=import_extra_handler)
