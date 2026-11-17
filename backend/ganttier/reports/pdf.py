"""Informe formal A4 mediante ReportLab Platypus."""
from __future__ import annotations

from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import CondPageBreak, Image, LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from ganttier.domain import ReportData
from .charts import draw
from .common import LIMIT, SEMÁFORO, cover, display, notice, sections, title, report_traffic, chart_description


def write(report: ReportData, path: Path) -> None:
    """Compone tablas paginadas, encabezados repetidos y gráficos a 150 dpi."""
    styles = getSampleStyleSheet()
    # Fuentes incluidas en matplotlib: tildes, Ω y símbolos técnicos sin depender
    # de fuentes instaladas en Windows ni sustituirlos por cuadrados negros.
    import matplotlib
    fonts = Path(matplotlib.get_data_path()) / 'fonts' / 'ttf'
    for suffix, filename in [('', 'DejaVuSans.ttf'), ('-Bold', 'DejaVuSans-Bold.ttf')]:
        if 'Ganttier' + suffix not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('Ganttier' + suffix, str(fonts / filename)))
    pdfmetrics.registerFontFamily('Ganttier', normal='Ganttier', bold='Ganttier-Bold', italic='Ganttier', boldItalic='Ganttier-Bold')
    for style in styles.byName.values():
        style.fontName = 'Ganttier-Bold' if 'Heading' in style.name or style.name == 'Title' else 'Ganttier'
    styles.add(ParagraphStyle("Cell", fontName="Ganttier", fontSize=7, leading=9, splitLongWords=True))
    styles.add(ParagraphStyle("CellHead", parent=styles["Cell"], textColor=colors.white, fontName="Ganttier-Bold"))
    styles["Heading1"].textColor = colors.HexColor("#17354A")
    width = A4[0] - 32 * mm
    story = [Spacer(1, 35 * mm), Paragraph(escape(title(report)), styles["Title"]), Spacer(1, 12 * mm)]
    for key, value in cover(report):
        story.extend([Paragraph(f"<b>{escape(key)}:</b> {escape(display(value))}", styles["Normal"]), Spacer(1, 4 * mm)])
    light, color = report_traffic(report)
    story.extend([Spacer(1, 8 * mm), Paragraph(f'<font color="#{color}"><b>Semáforo de plazo: {light}</b></font>', styles["Normal"]), Paragraph(escape(SEMÁFORO), styles["Normal"]), PageBreak()])

    def paragraph(value, header=False, percent=False):
        return Paragraph(escape(display(value, percent)).replace("\n", "<br/>"), styles["CellHead" if header else "Cell"])

    for section in sections(report):
        if section.chart:
            story.append(CondPageBreak(width / 2 + 55))
        story.append(Paragraph(escape(section.title), styles["Heading1"]))
        if section.chart:
            story.append(Image(BytesIO(draw(report, section.chart)), width=width, height=width / 2))
        if (section.total_rows or len(section.rows)) > LIMIT:
            story.append(Paragraph(notice(section.total_rows or len(section.rows)), styles["Normal"]))
        if section.rows:
            data = [[paragraph(h, True) for h in section.headers]]
            data.extend([paragraph(v, percent=i in section.percent_columns) for i, v in enumerate(row)] for row in section.rows[:LIMIT])
            weights = [1.0] * len(section.headers)
            if len(weights) >= 7:
                weights[0] = 2.5
            table = LongTable(data, colWidths=[width * weight / sum(weights) for weight in weights], repeatRows=1, hAlign="LEFT", splitInRow=1)
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17354A")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F5F8")]), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#D6E0E7")), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            story.append(table)
        else:
            story.append(Paragraph("Sin registros para el período.", styles["Normal"]))
        story.append(Spacer(1, 5 * mm))
    story.extend([Paragraph("Notas", styles["Heading1"]), Paragraph(escape(display(report.notes or "Sin notas.")).replace("\n", "<br/>"), styles["Normal"])])

    def page_frame(canvas, doc):
        canvas.saveState()
        canvas.setFont("Ganttier", 8)
        canvas.setFillColor(colors.HexColor("#17354A"))
        canvas.drawString(16 * mm, A4[1] - 12 * mm, "GANTTIER · " + title(report))
        canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"Página {doc.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=20 * mm, bottomMargin=18 * mm, title=title(report), author="Ganttier")
    document.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
