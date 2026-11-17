"""Informe Excel secuencial con detalle completo y gráficos nativos editables."""
from datetime import date, datetime
from dataclasses import replace
from io import BytesIO
from itertools import zip_longest
from pathlib import Path
import warnings

from openpyxl.cell import WriteOnlyCell
from openpyxl.chart import BarChart, LineChart, Reference, Series
from openpyxl.chart.axis import DateAxis
from openpyxl.chart.data_source import AxDataSource, NumRef
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo
from openpyxl.worksheet.filters import AutoFilter

from ganttier.domain import ReportData
from ganttier.excel_io.streaming import StreamingWorkbook as Workbook, create_sheet, close_workbook
from .common import SEMÁFORO, clean_text, cover, cutoff, display, finite, sections, title, report_traffic

FONT = Font(name='Arial', size=10)
HEADER_FONT = Font(name='Arial', size=10, bold=True, color='FFFFFF')
HEADER_FILL = PatternFill('solid', fgColor='17354A')
ALIGN = Alignment(vertical='top', wrap_text=True)


def _cell(sheet, value, percent=False, header=False):
    if isinstance(value, float) and not finite(value):
        value = None
    if value is not None and percent:
        value /= 100
    if value is not None and not isinstance(value, (str, int, float, bool, date, datetime)):
        value = display(value)
    if isinstance(value, str):
        value = clean_text(value)
        if len(value) > 32767:
            raise ValueError('Un texto del informe supera los 32.767 caracteres por celda de Excel; use HTML o divida el texto.')
    cell = WriteOnlyCell(sheet, value=value)
    if isinstance(value, str):
        cell.data_type = 's'
    light = value if isinstance(value, str) and value in {'Verde', 'Amarillo', 'Rojo', 'Sin datos'} else None
    number_format = 'General'
    if header:
        pass
    elif isinstance(value, datetime):
        number_format = 'dd-mm-yyyy hh:mm'
    elif isinstance(value, date):
        number_format = 'dd-mm-yyyy'
    elif percent:
        number_format = '0.0%'
    elif isinstance(value, float):
        number_format = '#,##0.00'
    elif isinstance(value, int) and not isinstance(value, bool):
        number_format = '#,##0'
    key = (header, number_format, light)
    if key in sheet._ganttier_styles:
        cell._style = sheet._ganttier_styles[key]
    else:
        cell.font = HEADER_FONT if header else FONT
        cell.alignment = ALIGN
        cell.number_format = number_format
        if header:
            cell.fill = HEADER_FILL
        elif light:
            cell.font = Font(name='Arial', size=11, bold=True, color={'Verde': '17734B', 'Amarillo': '956400', 'Rojo': 'B33135', 'Sin datos': '687787'}[light])
        sheet._ganttier_styles[key] = cell._style
    return cell


def _style(sheet, headers, rows, number, percentages=(), auxiliary=(), widths=None):
    sheet._ganttier_styles = {}
    # Registrar una vez los estilos; las filas sólo contienen valores crudos.
    for value in ('', 1, 1.5, True, date(2000, 1, 1), datetime(2000, 1, 1)):
        sheet.value_styles[type(value)] = _cell(sheet, value).style_id
    for value in ('Verde', 'Amarillo', 'Rojo', 'Sin datos'):
        sheet.text_styles[value] = _cell(sheet, value).style_id
    widths = widths or [38 if i == 0 else 23 for i in range(len(headers))]
    for i, width in enumerate(widths, 1):
        dimension = sheet.column_dimensions[get_column_letter(i)]
        dimension.width = width
        dimension.font = FONT
        dimension.alignment = ALIGN
        if i - 1 in percentages:
            dimension.number_format = '0.0%'
            sheet.column_styles[dimension.style_id] = dimension.style_id
    sheet.row_dimensions[1].height = 32
    sheet.freeze_panes = 'B2'
    sheet.sheet_view.showGridLines = False
    sheet.print_title_rows = '1:1'
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = '9'  # ECMA-376: A4, también en WriteOnlyWorksheet.
    sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.oddHeader.center.text = 'GANTTIER · ' + sheet.title
    sheet.oddFooter.right.text = 'Página &P de &N'
    sheet.sheet_format.defaultRowHeight = 32
    aux = iter(auxiliary)
    extra_header = next(aux, [])
    sheet.append([_cell(sheet, v, header=True) for v in headers] + [_cell(sheet, v, header=True) for v in extra_header])
    count = 0
    for row, extra in zip_longest(rows, aux, fillvalue=None):
        count += 1
        row = row if row is not None else [None] * len(headers)
        values = [_value(v, i in percentages) for i, v in enumerate(row)] + [_value(v) for v in (extra or [])]
        lines = max((sum(max(1, (len(line) + int(width) - 1) // int(width)) for line in v.split('\n'))
                     for v, width in zip(values, widths) if isinstance(v, str)), default=1)
        if lines > 2:
            sheet.row_dimensions[count + 1].height = min(409, lines * 13)
        sheet.append(values)
    if not count:
        sheet.append([None] * len(headers))
    ref = f'A1:{get_column_letter(len(headers))}{max(2, len(rows) + 1)}'
    # El filtro lo declara sólo la tabla: un autoFilter de hoja sobre el mismo rango (y su
    # _xlnm._FilterDatabase) hace que Excel pida reparar el libro y quite las tablas (D01).
    table = Table(displayName=f'Informe_{number}', ref=ref, autoFilter=AutoFilter(ref=ref),
                  tableColumns=[TableColumn(id=i, name=h) for i, h in enumerate(headers, 1)])
    table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='In write-only mode you must add table columns manually')
        sheet.add_table(table)


def _value(value, percent=False):
    if isinstance(value, float) and not finite(value):
        return None
    if value is not None and percent:
        value /= 100
    if value is not None and not isinstance(value, (str, int, float, bool, date, datetime)):
        value = display(value)
    return clean_text(value) if isinstance(value, str) else value


def _curve_aux(report):
    cut = cutoff(report)
    if cut is None:
        return []
    top = max([1.] + [float(v) / 100 for name in ('planned_cum', 'earned_cum', 'actual_cum', 'forecast_cum') for v in getattr(report.scurve, name) if finite(v)])
    return [[None, 'Fecha de corte', 'Corte'], [None, cut, 0], [None, cut, top]]


def _scurve(sheet, report, source=None):
    source = source if source is not None else sheet
    n = len(report.scurve.labels)
    if not n:
        return
    chart = LineChart()
    chart.visible_cells_only = source is sheet
    chart.title = 'Curva S'
    chart.y_axis.title, chart.y_axis.numFmt = 'Porcentaje del presupuesto', '0%'
    chart.x_axis = DateAxis(crossAx=100)
    chart.x_axis.axPos = 'b'
    chart.y_axis.crossAx = chart.x_axis.axId  # sin esto apuntaba al eje 10, inexistente: Excel descartaba el gráfico (D01)
    chart.x_axis.number_format, chart.x_axis.title = 'dd-mm-yyyy', 'Fecha'
    chart.add_data(Reference(source, min_col=2, max_col=5, min_row=1, max_row=n + 1), titles_from_data=True)
    chart.set_categories(Reference(source, min_col=1, min_row=2, max_row=n + 1))
    chart.display_blanks = 'gap'
    if cutoff(report):
        series = Series(Reference(source, min_col=8, min_row=2, max_row=3), title='Fecha de corte')
        series.cat = AxDataSource(numRef=NumRef(f"'{source.title}'!$G$2:$G$3"))
        series.graphicalProperties.line.solidFill = '465563'
        series.graphicalProperties.line.prstDash = 'sysDot'
        chart.append(series)
    chart.width, chart.height = 25, 12
    sheet.add_chart(chart, 'K2')


def _load_aux(report):
    from .charts import load_chart_data
    groups, capacity = load_chart_data(report.load)
    yield [None, None, 'Período', *[name for name, _ in groups], 'Capacidad total']
    for i, label in enumerate(report.load.labels):
        yield [None, None, date.fromisoformat(label), *[float(values[i]) for _, values in groups], float(capacity[i])]


def _load(sheet, report, source=None):
    source = source if source is not None else sheet
    profile = report.load
    if not profile.labels or not profile.series:
        return
    capacity_col = 8 + min(8, len(profile.series)) + (len(profile.series) > 8)
    end = len(profile.labels) + 1
    bars = BarChart()
    bars.visible_cells_only = source is sheet
    bars.type, bars.grouping, bars.overlap = 'col', 'stacked', 100
    bars.title, bars.y_axis.title = 'Carga por recurso o rol y capacidad', 'HH'
    bars.add_data(Reference(source, min_col=8, max_col=capacity_col - 1, min_row=1, max_row=end), titles_from_data=True)
    bars.set_categories(Reference(source, min_col=7, min_row=2, max_row=end))
    bars.x_axis.numFmt = 'dd-mm-yyyy'
    capacity = LineChart()
    capacity.add_data(Reference(source, min_col=capacity_col, min_row=1, max_row=end), titles_from_data=True)
    capacity.set_categories(Reference(source, min_col=7, min_row=2, max_row=end))
    capacity.series[0].graphicalProperties.line.solidFill = '263747'
    capacity.series[0].graphicalProperties.line.prstDash = 'sysDash'
    bars += capacity
    bars.display_blanks = 'gap'
    bars.width, bars.height = 25, 12
    sheet.add_chart(bars, 'L2')


def _chart_source(workbook, report, kind):
    """El detalle permanece intacto; los gráficos sólo usan semanas/meses.

    Curvas acumuladas: último valor del período (incluidos huecos).
    Carga/capacidad: suma del período; una ausencia deja el total incompleto.
    """
    data = report.scurve if kind == 'scurve' else report.load
    if data.period in ('week', 'month') and len(data.labels) <= 520:
        return report, None
    if not data.labels:
        return report, None
    groups = {}
    period = 'week' if len(data.labels) <= 3660 else 'month'
    for i, label in enumerate(data.labels):
        day = date.fromisoformat(label)
        key = day.isocalendar()[:2] if period == 'week' else (day.year, day.month)
        groups.setdefault(key, []).append(i)
    indexes = list(groups.values())
    # Incluso horizontes de siglos mantienen acotado el gráfico. Agrupar
    # períodos consecutivos conserva todos los meses/semanas y sus totales.
    if len(indexes) > 520:
        stride = (len(indexes) + 519) // 520
        indexes = [[i for group in indexes[start:start + stride] for i in group]
                   for start in range(0, len(indexes), stride)]
    if kind == 'scurve' and data.data_date is not None:
        cutoff = data.data_date.isoformat()
        # Divide el grupo al corte: nunca traslada EV/AC a una fecha futura.
        indexes = [part for group in indexes
                   for part in ([i for i in group if data.labels[i] <= cutoff],
                                [i for i in group if data.labels[i] > cutoff]) if part]
    labels = [data.labels[group[-1]] for group in indexes]
    sheet = create_sheet(workbook, '_Grafico_Curva' if kind == 'scurve' else '_Grafico_Carga')
    sheet.sheet_state = 'hidden'
    if kind == 'scurve':
        names = ('planned_cum', 'earned_cum', 'actual_cum', 'forecast_cum',
                 'planned_abs', 'earned_abs', 'actual_abs', 'forecast_abs')
        curve = replace(data, period=period, labels=labels,
                        **{name: [getattr(data, name)[group[-1]] for group in indexes] for name in names})
        aggregated = replace(report, scurve=curve)
        rows = [[date.fromisoformat(label), *values] for label, *values in
                zip(labels, curve.planned_cum, curve.earned_cum, curve.actual_cum, curve.forecast_cum)]
        _style(sheet, ['Fecha', 'Planificado %', 'Ganado %', 'Real %', 'Proyección %'], rows,
               100, (1, 2, 3, 4), _curve_aux(aggregated))
    else:
        def totals(values):
            return [sum(values[i] for i in group) if all(finite(values[i]) for i in group) else None
                    for group in indexes]
        profile = replace(data, period=period, labels=labels,
                          series=[replace(s, load=totals(s.load), capacity=totals(s.capacity)) for s in data.series])
        aggregated = replace(report, load=profile)
        _style(sheet, ['Período', 'Recurso / rol', 'Carga (HH)', 'Capacidad (HH)'], [],
               101, auxiliary=_load_aux(aggregated))
    return aggregated, sheet


def _populate(workbook, report):
    front = create_sheet(workbook, 'Portada')
    light, _ = report_traffic(report)
    _style(front, [title(report), 'Datos del informe'], [list(r) for r in cover(report)] + [['Semáforo de plazo', light], ['Criterio', SEMÁFORO]], 1, widths=[44, 80])
    for number, section in enumerate(sections(report, full_detail=True), 2):
        sheet = create_sheet(workbook, section.title[:31])
        aux = _curve_aux(report) if section.chart == 'scurve' else _load_aux(report) if section.chart == 'load' else ()
        _style(sheet, section.headers, section.rows, number, section.percent_columns, aux,
               [35, 105] if section.title == 'Resumen ejecutivo' else None)
        if section.chart == 'scurve':
            aggregated, source = _chart_source(workbook, report, 'scurve')
            _scurve(sheet, aggregated, source)
        elif section.chart == 'load':
            aggregated, source = _chart_source(workbook, report, 'load')
            _load(sheet, aggregated, source)
        elif section.chart == 'performance' and report.performance:
            chart = BarChart()
            chart.type, chart.title = 'bar', 'Cumplimiento por trabajador · primeros 20'
            chart.y_axis.title, chart.y_axis.numFmt = 'Cumplimiento (%)', '0%'
            end = min(20, len(report.performance)) + 1
            chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=end), titles_from_data=True)
            chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=end))
            chart.width, chart.height = 24, 13
            sheet.add_chart(chart, 'J2')
        elif section.chart in ('gantt', 'skill_load'):
            from .charts import draw
            picture = Image(BytesIO(draw(report, section.chart)))
            picture.width, picture.height = 850, 425
            sheet.add_image(picture, 'I2')
    notes = create_sheet(workbook, 'Notas')
    _style(notes, ['Notas'], [[report.notes or 'Sin notas.']], 99, widths=[110])


def write(report: ReportData, path: Path) -> None:
    workbook = Workbook(write_only=True)
    workbook._ganttier_temp_dir = Path(path).parent
    try:
        _populate(workbook, report)
        workbook.save(path)
    finally:
        close_workbook(workbook)
