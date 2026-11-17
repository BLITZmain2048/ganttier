from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile
import runpy
import pytest
from openpyxl import load_workbook
from ganttier.reports import render
from ganttier.reports.common import sections, LIMIT

@pytest.fixture
def sample():
    return runpy.run_path(str(Path(__file__).with_name('test_reports.py')))['report'].__wrapped__()

@pytest.mark.parametrize('period',['weekly','monthly','annual'])
def test_r3_equivalence(sample,tmp_path,period):
    from docx import Document
    from PySide6.QtGui import QGuiApplication
    from ganttier.importadores.pdf_texto import extraer_paginas
    app=QGuiApplication.instance() or QGuiApplication([])
    sample.period_type=period
    paths={fmt:render(sample,fmt,tmp_path/f'r.{fmt}') for fmt in ('pdf','docx','html','xlsx')}
    html=paths['html'].read_text(encoding='utf-8')
    doc=Document(paths['docx'])
    text_doc=' '.join(p.text for p in doc.paragraphs)+' '.join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    pages,_=extraer_paginas(paths['pdf']); text_pdf=' '.join(pages)
    wb=load_workbook(paths['xlsx'])
    summary=dict(wb['Resumen ejecutivo'].values)
    assert summary['SPI']==.8 and summary['CPI']==pytest.approx(40/45)
    assert summary['Semáforo de costo (CPI)']=='Amarillo'
    for text in (html,text_doc,text_pdf):
        for needle in ('0,80','0,89','112,50','Amarillo','R3 / 1','H001'):
            assert needle in text
    assert 'scope="col"' in html and 'huecos = sin datos' in html
    with ZipFile(paths['docx']) as z:
        assert b'descr=' in z.read('word/document.xml')
    wb.close()

def test_r3_bounded_delayed(sample):
    sample.delayed=[replace(sample.delayed[0],code=f'D{i}',delay_days=i) for i in range(100000)]
    section=next(s for s in sections(sample) if s.title=='Atrasadas')
    assert len(section.rows)==LIMIT and section.total_rows==100000
    assert section.rows[0][0].startswith('D99999')
    full=next(s for s in sections(sample,full_detail=True) if s.title=='Atrasadas')
    assert len(full.rows)==100000

def test_r3_additive_fields(sample,tmp_path):
    sample.critical=[sample.scheduled_in_period[0]]
    sample.skill_load=sample.load
    for fmt in ('pdf','docx','html','xlsx'):
        render(sample,fmt,tmp_path/f'skills.{fmt}')
    wb=load_workbook(tmp_path/'skills.xlsx')
    assert wb['Actividades críticas']['A2'].value.startswith('A001')
    assert wb['Carga semanal por habilidad']['C2'].value==sample.skill_load.series[0].load[0]
    wb.close()

@pytest.mark.slow
def test_r3_pdf_100000(sample,tmp_path):
    import json
    from time import perf_counter
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtPdf import QPdfDocument
    from ganttier.importadores.pdf_texto import extraer_paginas
    app=QGuiApplication.instance() or QGuiApplication([])
    sample.scheduled_in_period=[replace(sample.scheduled_in_period[0],code=f'A{i:06}') for i in range(100000)]
    started=perf_counter()
    path=render(sample,'pdf',tmp_path/'large.pdf')
    elapsed=perf_counter()-started
    pages,_=extraer_paginas(path)
    text=' '.join(pages)
    assert 'A000499' in text and 'A000500' not in text
    assert len(pages)<150
    (tmp_path/'benchmark_pdf.json').write_text(json.dumps({'activities':100000,'seconds':elapsed,'pages':len(pages)}))
    print(f'PDF 100000: {elapsed:.3f}s, {len(pages)} páginas')
    assert elapsed<60
