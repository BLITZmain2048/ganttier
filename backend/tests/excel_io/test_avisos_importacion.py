"""D02 (verificación del .exe, 08-10-2026): la validación avisa de vínculos externos y de proyectos muy grandes."""
from zipfile import ZipFile, ZIP_DEFLATED

from ganttier.excel_io import build_project_template, parse_workbook
from ganttier.excel_io import importer


def _con_vinculo_externo(origen, destino):
    with ZipFile(origen) as fuente, ZipFile(destino, 'w', ZIP_DEFLATED) as salida:
        for item in fuente.infolist():
            salida.writestr(item, fuente.read(item.filename))
        salida.writestr('xl/externalLinks/externalLink1.xml',
                        '<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><externalBook/></externalLink>')
    return destino


def test_vinculo_externo_se_advierte_sin_bloquear(tmp_path):
    limpio = parse_workbook(build_project_template(tmp_path / 'base.xlsx'))
    assert not [i for i in limpio.issues if i.sheet == 'Libro']
    bundle = parse_workbook(_con_vinculo_externo(tmp_path / 'base.xlsx', tmp_path / 'vinculo.xlsx'))
    avisos = [i for i in bundle.issues if i.sheet == 'Libro']
    assert len(avisos) == 1 and avisos[0].severity == 'advertencia' and '1 vínculo' in avisos[0].message
    assert not bundle.has_errors  # se advierte, no se bloquea: los valores guardados siguen siendo legibles
    assert bundle.activities == limpio.activities


def test_proyecto_sobre_el_tamano_verificado_se_advierte(tmp_path, monkeypatch):
    path = build_project_template(tmp_path / 'base.xlsx')
    total = len(parse_workbook(path).activities)
    monkeypatch.setattr(importer, 'LARGE_PROJECT_ACTIVITIES', total - 1)
    avisos = [i for i in parse_workbook(path).issues if i.sheet == 'Actividades' and i.field == 'Cantidad']
    assert len(avisos) == 1 and avisos[0].severity == 'advertencia' and 'verificó' in avisos[0].message
    monkeypatch.setattr(importer, 'LARGE_PROJECT_ACTIVITIES', total)
    assert not [i for i in parse_workbook(path).issues if i.field == 'Cantidad']
