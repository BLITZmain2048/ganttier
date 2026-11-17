"""Verificación estructural y semántica de los cuatro formatos."""
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile
import importlib.util
import xml.etree.ElementTree as ET

import pytest
from openpyxl import load_workbook

from ganttier.domain import EVMResult, LoadProfile, LoadSeries, Project, ReportData, ReportRow, SCurve, WorkerKPI
from ganttier.reports import render
from ganttier.reports.common import traffic_light


@pytest.fixture
def report():
    """Corte semanal con avance, atraso, carga y proyección plausibles."""
    d = date(2026, 10, 9)
    project = Project(1, "Ampliación S/E Cordillera 220 kV", start_date=date(2026, 10, 1), end_date=date(2026, 10, 31), data_date=d)
    evm = EVMResult(d, "hours", 100, 50, 40, 45, -10, -5, .8, 40/45, 6.4, .8, 112.5, 67.5, -12.5, 60/55, date(2026, 10, 31), date(2026, 11, 5), 50, 40)
    curve = SCurve("week", "hours", ["2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23", "2026-10-31", "2026-11-05"], [20, 50, 65, 80, 100, 100], [15, 40, None, None, None, None], [18, 45, None, None, None, None], [None, 40, 55, 70, 90, 100], [20, 50, 65, 80, 100, 100], [15, 40, None, None, None, None], [18, 45, None, None, None, None], [None, 40, 55, 70, 90, 100], 100, d, date(2026, 11, 5), date(2026, 10, 31))
    load = LoadProfile("week", ["2026-10-05", "2026-10-12", "2026-10-19"], [LoadSeries("Técnica Protecciones B", [40, 40, 40], [32, 44, 24]), LoadSeries("Técnico SSEE A", [40, 40, 40], [24, 36, 16])])
    performance = [WorkerKPI(1, "Técnica Protecciones B", 5, 3, 2, 4, 50, 32, 24, 28, 24/28, 2, .75, .86, 2)]
    row = ReportRow("A001", "Pruebas de protecciones del paño J1", date(2026, 10, 1), d, 100, "terminada", "Técnica Protecciones B", date(2026, 10, 7), 2, "S/E Cordillera")
    delayed = [replace(row, code="A002", percent_complete=50, status="en_curso", delay_days=3), replace(row, code="A003", delay_days=8)]
    upcoming = [replace(row, code="A004", name="Energización del transformador", start=date(2026, 10, 12), finish=date(2026, 10, 14), percent_complete=0, status="no_iniciada")]
    return ReportData(project, "weekly", date(2026, 10, 5), date(2026, 10, 11), datetime(2026, 10, 9, 18, 0), evm, curve, load, performance, [row], [row], delayed, upcoming, [replace(row, code="H001", name="Puesta en servicio", start=d, finish=d)], {"Responsable del informe": "Oficina técnica"}, "Coordinar ventana de desconexión.\nDatos de ejemplo ficticios.")


@pytest.mark.parametrize("fmt,dependency", [("xlsx", "openpyxl"), ("html", "matplotlib"), ("pdf", "reportlab"), ("docx", "docx")])
def test_render_formats(report, tmp_path, fmt, dependency):
    if importlib.util.find_spec(dependency) is None:
        pytest.skip(f"Dependencia no instalada: {dependency}")
    target = tmp_path / f"report.{fmt}"
    assert render(report, fmt, target) == target
    assert target.stat().st_size > 3000
    if fmt == "xlsx":
        workbook = load_workbook(target)
        assert "Resumen ejecutivo" in workbook.sheetnames
        assert len(workbook["Curva S"]._charts) == 1
        assert len(workbook["Curva S"]._charts[0].series) == 5
        assert workbook["Curva S"]["B2"].value == .2
        assert workbook["Curva S"]["B2"].number_format == "0.0%"
        assert workbook["Atrasadas"]["A2"].value.startswith("A003")
        assert workbook["Carga por recurso o rol"]._charts
        assert workbook["Desempeño por trabajador"]._charts
        workbook.close()
    elif fmt == "html":
        content = target.read_text(encoding="utf-8")
        assert "data:image/png;base64," in content
        assert "@media print" in content
        assert "Informe semanal de avance" in content
        assert content.count('<img ') == 4
        assert "src=\"http" not in content
    elif fmt == "docx":
        from docx import Document
        document = Document(target)
        assert len(document.inline_shapes) == 4
        assert len(document.tables) >= 10
        assert "Notas" in [p.text for p in document.paragraphs]
        with ZipFile(target) as archive:
            ET.fromstring(archive.read("word/document.xml"))
    else:
        assert target.read_bytes().startswith(b"%PDF")
        if importlib.util.find_spec("pypdf"):
            from pypdf import PdfReader
            reader = PdfReader(target)
            text = "\n".join(page.extract_text() for page in reader.pages)
            for needle in ["Informe semanal de avance", "Curva S", "EVM", "Lookahead", "Notas", "Página"]:
                assert needle in text
            assert all(abs(float(p.mediabox.width) - 595.276) < 1 for p in reader.pages)


@pytest.mark.parametrize("spi,label", [(None, "Sin datos"), (.95, "Verde"), (.9499, "Amarillo"), (.85, "Amarillo"), (.8499, "Rojo")])
def test_traffic_thresholds(spi, label):
    assert traffic_light(spi)[0] == label


def test_html_escapes_and_limits(report, tmp_path):
    report.notes = '<script>alert("x")</script>'
    report.delayed = [replace(report.delayed[0], code=f"L{i:04}", delay_days=999-i) for i in range(501)]
    content = render(report, "html", tmp_path / "long.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;" in content and "<script>" not in content
    assert "primeras 500 de 501" in content
    assert "L0499" in content and "L0500" not in content
    workbook = load_workbook(render(report, "xlsx", tmp_path / "long.xlsx"))
    assert workbook["Atrasadas"].max_row == 502
    workbook.close()


def test_atomic_failure_and_invalid_inputs(report, tmp_path):
    path = tmp_path / "keep.html"
    path.write_text("Original", encoding="utf-8")
    with pytest.raises(ValueError, match="Formato"):
        render(report, "exe", path)
    report.load.series[0].load = []
    with pytest.raises(ValueError, match="carga"):
        render(report, "html", path)
    assert path.read_text(encoding="utf-8") == "Original"


def test_empty_report(report, tmp_path):
    for name in ("scheduled_in_period", "completed_in_period", "delayed", "lookahead", "milestones", "performance"):
        setattr(report, name, [])
    report.load = LoadProfile("week", [], [])
    for name in ("labels", "planned_cum", "earned_cum", "actual_cum", "forecast_cum", "planned_abs", "earned_abs", "actual_abs", "forecast_abs"):
        setattr(report.scurve, name, [])
    for fmt, dependency in [("html", "matplotlib"), ("xlsx", "openpyxl"), ("pdf", "reportlab"), ("docx", "docx")]:
        if importlib.util.find_spec(dependency):
            render(report, fmt, tmp_path / f"empty.{fmt}")


@pytest.mark.parametrize("period,text", [("monthly", "mensual"), ("annual", "anual")])
def test_period_titles(report, tmp_path, period, text):
    report.period_type = period
    assert f"Informe {text} de avance" in render(report, "html", tmp_path / "period.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("fmt,dependency", [("pdf", "reportlab"), ("docx", "docx")])
def test_long_tables_in_formal_formats(report, tmp_path, fmt, dependency):
    if importlib.util.find_spec(dependency) is None:
        pytest.skip(f"Dependencia no instalada: {dependency}")
    report.delayed = [replace(report.delayed[0], code=f"L{i:04}", delay_days=999-i) for i in range(501)]
    path = render(report, fmt, tmp_path / f"long.{fmt}")
    if fmt == "docx":
        from docx import Document
        doc = Document(path)
        assert any("primeras 500 de 501" in p.text for p in doc.paragraphs)
        assert any(len(t.rows) == 501 for t in doc.tables)
        assert all("L0500" not in c.text for t in doc.tables for r in t.rows for c in r.cells)
    elif importlib.util.find_spec("pypdf"):
        from pypdf import PdfReader
        text = "\n".join(p.extract_text() for p in PdfReader(path).pages)
        assert "primeras 500 de 501" in text
        assert "L0499" in text and "L0500" not in text


def test_failed_writer_does_not_replace_existing(report, tmp_path, monkeypatch):
    from ganttier.reports import html
    def fail(report, path):
        path.write_text("Parcial", encoding="utf-8")
        raise OSError("Disco no disponible")
    monkeypatch.setattr(html, "write", fail)
    path = tmp_path / "existing.html"
    path.write_text("Original", encoding="utf-8")
    with pytest.raises(OSError, match="guardar"):
        render(report, "html", path)
    assert path.read_text(encoding="utf-8") == "Original"
    assert list(tmp_path.iterdir()) == [path]


def test_spreadsheet_text_never_becomes_formula(report, tmp_path):
    report.load.series[0].key = '=HYPERLINK("https://example.com","Recurso")'
    report.notes = '=2+2'
    workbook = load_workbook(render(report, "xlsx", tmp_path / "literal.xlsx"))
    assert not any(c.data_type == "f" for sheet in workbook for row in sheet for c in row)
    workbook.close()


def test_executive_conclusions_and_chilean_integers(report):
    from ganttier.reports.common import sections, display
    summary = sections(report)[0]
    text = '\n'.join(str(v) for row in summary.rows for v in row)
    assert '5 días' in text and 'SPI(t)=0,80' in text
    assert text.index('A003') < text.index('A002')
    assert '4,00 HH' in text  # overload of one worker masked by total capacity
    assert display(1234567) == '1.234.567'


def test_conclusions_do_not_invent_baseline_or_delay(report):
    from ganttier.reports.common import sections
    report.evm.planned_finish = report.evm.forecast_finish = None
    report.evm.SPI_t = None
    report.delayed = []
    text = '\n'.join(str(v) for row in sections(report)[0].rows for v in row)
    assert 'No se puede determinar' in text and 'SPI(t)=—' in text


@pytest.mark.parametrize('fmt', ['xlsx', 'html', 'pdf', 'docx'])
def test_optional_values_special_characters_and_long_text(report, tmp_path, fmt):
    if fmt in ('pdf', 'docx'):
        pytest.importorskip('reportlab' if fmt == 'pdf' else 'docx')
    report.load.series[0].capacity[1] = None
    report.load.series[0].load[1] = None
    report.delayed[0].name = 'S/E & <paño> ñ Ω ≥ ' + 'Termografía ' * 700
    report.delayed[0].responsible = report.delayed[0].location = None
    report.kpis['Detalle'] = {'origen': 'S/E & <paño>'}
    render(report, fmt, tmp_path / f'adversarial.{fmt}')


def test_load_dates_are_dates_and_missing_totals_are_not_zero(report, tmp_path):
    report.load.series[0].capacity[1] = None
    wb = load_workbook(render(report, 'xlsx', tmp_path / 'load.xlsx'))
    assert wb['Carga por recurso o rol']['A2'].number_format == 'dd-mm-yyyy'
    assert wb['Carga por recurso o rol']['J3'].value is None
    wb.close()


@pytest.mark.slow
def test_xlsx_100000_rows_streamed(report, tmp_path, monkeypatch):
    from time import perf_counter
    from ganttier.reports import xlsx
    real_workbook = xlsx.Workbook
    def streaming_workbook(*args, **kwargs):
        assert kwargs.get('write_only') is True
        return real_workbook(*args, **kwargs)
    monkeypatch.setattr(xlsx, 'Workbook', streaming_workbook)
    report.scheduled_in_period = [replace(report.scheduled_in_period[0], code=f'A{i:06d}') for i in range(100000)]
    start = perf_counter()
    path = render(report, 'xlsx', tmp_path / '100000.xlsx')
    elapsed = perf_counter() - start
    print(f'\nInforme XLSX 100.000 filas: {elapsed:.3f} s')
    (tmp_path / 'benchmark.json').write_text(__import__('json').dumps({'rows': 100000, 'elapsed_s': elapsed, 'limit_s': 25}), encoding='utf-8')
    assert elapsed < 25, f'Informe: {elapsed:.3f} s; límite: 25 s'
    wb = load_workbook(path, read_only=True)
    rows = wb['Programadas en el período'].iter_rows(min_row=2, values_only=True)
    count, last = 0, None
    for last in rows:
        count += 1
    assert count == 100000 and last[0].startswith('A099999')
    wb.close()


def test_completed_late_project_does_not_get_green_spi(report):
    from ganttier.reports.common import sections
    report.evm.SPI, report.evm.SPI_t = 1., .7
    summary = sections(report)[0]
    assert any('Semáforo' in row[0] and row[1] == 'Rojo' for row in summary.rows)


def test_chart_groups_resources_without_losing_load_and_capacity(report):
    from ganttier.reports.charts import load_chart_data
    report.load = LoadProfile('day', ['2026-01-01'], [LoadSeries(f'R{i}', [8], [i]) for i in range(20)])
    groups, capacity = load_chart_data(report.load)
    assert len(groups) == 9 and groups[-1][0] == 'Otros recursos'
    assert sum(values[0] for _, values in groups) == sum(range(20))
    assert capacity[0] == 160


def test_streamed_failure_preserves_original_and_cleans_temporary_xml(report, tmp_path, monkeypatch):
    from openpyxl.worksheet import _writer
    report.notes = 'x' * 32768
    old_temps = set(_writer.ALL_TEMP_FILES)
    path = tmp_path / 'existing.xlsx'
    path.write_bytes(b'original')
    with pytest.raises(ValueError, match='32.767'):
        render(report, 'xlsx', path)
    assert path.read_bytes() == b'original'
    assert set(_writer.ALL_TEMP_FILES) == old_temps


def test_pdf_unicode_is_extractable(report, tmp_path):
    pytest.importorskip('reportlab')
    report.notes = 'Protección Ω ≥ 10; termografía y paño Ñ.'
    path = render(report, 'pdf', tmp_path / 'unicode.pdf')
    try:
        import pypdf
    except ImportError:
        # La distribución de A incluye QtPdf como lector local. Comprobar la
        # misma propiedad Unicode también cuando pypdf no está instalado.
        pytest.importorskip('PySide6.QtPdf')
        from ganttier.importadores.pdf_texto import extraer_paginas
        pages, _reader = extraer_paginas(path)
        content = '\n'.join(pages)
    else:
        content = '\n'.join(p.extract_text() for p in pypdf.PdfReader(path).pages)
    assert report.notes in content


def test_daily_native_charts_use_weekly_sources_and_keep_detail(report, tmp_path):
    from datetime import timedelta
    labels = [(date(2026, 10, 5) + timedelta(days=i)).isoformat() for i in range(14)]
    report.scurve.period = report.load.period = 'day'
    report.scurve.labels = report.load.labels = labels
    for name in ('planned_cum', 'earned_cum', 'actual_cum', 'forecast_cum',
                 'planned_abs', 'earned_abs', 'actual_abs', 'forecast_abs'):
        setattr(report.scurve, name, list(range(1, 15)))
    report.load.series = [LoadSeries('Técnica', [8] * 14, [6] * 14)]
    report.load.series[0].capacity[-1] = None
    wb = load_workbook(render(report, 'xlsx', tmp_path / 'daily.xlsx'))
    assert wb['Curva S'].max_row == 15
    assert wb['Carga por recurso o rol'].max_row == 15
    curve, load = wb['_Grafico_Curva'], wb['_Grafico_Carga']
    assert curve.sheet_state == load.sheet_state == 'hidden'
    # El corte del viernes es una muestra obligatoria además de los fines semanales.
    assert curve['A2'].value.date() == report.scurve.data_date
    assert [curve[f'B{row}'].value for row in (2, 3, 4)] == [.05, .07, .14]
    assert load['H2'].value == load['H3'].value == 42
    assert load['I2'].value == 56 and load['I3'].value is None
    assert '_Grafico_Curva' in wb['Curva S']._charts[0].series[0].val.numRef.f
    assert '_Grafico_Carga' in wb['Carga por recurso o rol']._charts[0].series[0].val.numRef.f
    assert wb['Curva S']._charts[0].visible_cells_only is False
    assert wb['Carga por recurso o rol']._charts[0].visible_cells_only is False
    assert wb['Curva S']['B2'].number_format == '0.0%'
    assert wb['Carga por recurso o rol']['A2'].number_format == 'dd-mm-yyyy'
    assert wb['Curva S'].auto_filter.ref is None  # el filtro lo declara sólo la tabla (D01)
    assert next(iter(wb['Curva S'].tables.values())).autoFilter.ref == 'A1:E15'
    wb.close()


def test_large_chart_source_is_monthly(report, tmp_path):
    from datetime import timedelta
    from ganttier.reports.xlsx import _chart_source
    from ganttier.excel_io.streaming import close_workbook
    from openpyxl import Workbook
    count = 20000
    report.scurve.period = 'day'
    report.scurve.labels = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(count)]
    for name in ('planned_cum', 'earned_cum', 'actual_cum', 'forecast_cum',
                 'planned_abs', 'earned_abs', 'actual_abs', 'forecast_abs'):
        setattr(report.scurve, name, list(range(count)))
    wb = Workbook(write_only=True)
    wb._ganttier_temp_dir = tmp_path
    try:
        aggregated, source = _chart_source(wb, report, 'scurve')
        assert aggregated.scurve.period == 'month'
        assert len(aggregated.scurve.labels) <= 520
        assert aggregated.scurve.planned_cum[-1] == count - 1
        assert len(report.scurve.labels) == count
    finally:
        close_workbook(wb)


@pytest.mark.parametrize('period', ['weekly', 'monthly', 'annual'])
def test_xlsx_no_requiere_reparacion_en_excel(report, tmp_path, period):
    """D01: autoFilter de hoja sobre una tabla y ejes de gráfico cruzados a un eje inexistente."""
    from ganttier.excel_io.compat import structure_problems
    path = render(replace(report, period_type=period), 'xlsx', tmp_path / f'{period}.xlsx')
    assert structure_problems(path) == []


def test_validador_estructural_detecta_las_causas_de_reparacion(report, tmp_path):
    """El validador no debe aprobar nunca el defecto original."""
    from zipfile import ZipFile, ZIP_DEFLATED
    from ganttier.excel_io.compat import structure_problems
    good = render(report, 'xlsx', tmp_path / 'good.xlsx')
    bad = tmp_path / 'bad.xlsx'
    with ZipFile(good) as source, ZipFile(bad, 'w', ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == 'xl/worksheets/sheet1.xml':
                data = data.replace(b'</sheetData>', b'</sheetData><autoFilter ref="A1:B2"/>', 1)
            if item.filename == 'xl/charts/chart1.xml':
                data = data.replace(b'<crossAx val="500"/>', b'<crossAx val="10"/>', 1)
            target.writestr(item, data)
    found = structure_problems(bad)
    assert any('autoFilter' in p for p in found) and any('no existe' in p for p in found)
