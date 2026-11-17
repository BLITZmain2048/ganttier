"""Informe editable en Word con tablas y gráficos embebidos."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ganttier.domain import ReportData
from .charts import draw
from .common import LIMIT, SEMÁFORO, cover, display, notice, sections, title, report_traffic, chart_description


def write(report: ReportData, path: Path) -> None:
    """Genera A4 con estilos de título, cabeceras repetidas y número de página."""
    document = Document()
    page = document.sections[0]
    page.page_width, page.page_height = Cm(21), Cm(29.7)
    page.left_margin = page.right_margin = Cm(1.6)
    page.top_margin = page.bottom_margin = Cm(1.8)
    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(10)
    document.styles["Normal"].paragraph_format.space_after = Pt(6)
    for name in ("Title", "Heading 1", "Heading 2"):
        document.styles[name].font.name = "Arial"
        document.styles[name].font.color.rgb = RGBColor.from_string("17354A")
    page.header.paragraphs[0].text = "GANTTIER · Control de avance"
    footer = page.footer.paragraphs[0]
    footer.text = "Página "
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    document.add_heading(title(report), 0)
    for key, value in cover(report):
        paragraph = document.add_paragraph()
        paragraph.add_run(key + ": ").bold = True
        paragraph.add_run(display(value))
    light, color = report_traffic(report)
    run = document.add_paragraph().add_run("Semáforo de plazo: " + light)
    run.bold = True
    run.font.color.rgb = RGBColor.from_string(color)
    document.add_paragraph(SEMÁFORO)
    document.add_page_break()
    for section in sections(report):
        document.add_heading(section.title, 1)
        if section.chart:
            picture = document.add_picture(BytesIO(draw(report, section.chart)), width=Cm(17.7))
            picture._inline.docPr.set('descr', chart_description(section.chart))
        if (section.total_rows or len(section.rows)) > LIMIT:
            document.add_paragraph(notice(section.total_rows or len(section.rows)))
        if not section.rows:
            document.add_paragraph("Sin registros para el período.")
            continue
        table = document.add_table(rows=1, cols=len(section.headers))
        table.style = "Light Shading Accent 1"
        table.autofit = False
        widths = [1.0] * len(section.headers)
        if len(widths) >= 7:
            widths[0] = 2.5
        for column, weight in zip(table.columns, widths):
            column.width = Cm(17.7 * weight / sum(widths))
        for cell, header in zip(table.rows[0].cells, section.headers):
            cell.text = header
        header_property = OxmlElement("w:tblHeader")
        table.rows[0]._tr.get_or_add_trPr().append(header_property)
        for record in section.rows[:LIMIT]:
            row = table.add_row()
            for idx, (cell, value) in enumerate(zip(row.cells, record)):
                cell.text = display(value, idx in section.percent_columns)
        for row in table.rows:
            for cell, weight in zip(row.cells, widths):
                cell.width = Cm(17.7 * weight / sum(widths))
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(3)
                    for run in paragraph.runs:
                        run.font.size = Pt(8)
        document.add_paragraph()
    document.add_heading("Notas", 1)
    document.add_paragraph(display(report.notes or "Sin notas."))
    document.core_properties.title = title(report)
    document.core_properties.author = "Ganttier"
    document.save(path)
