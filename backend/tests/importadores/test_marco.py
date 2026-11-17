"""Aceptación contra libros reales y variantes, sin modificar las referencias."""
from pathlib import Path
from decimal import Decimal
import pytest
import openpyxl

from ganttier.importadores import detectar, previsualizar, Perfil, AlmacenPerfiles

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file() and (p / 'tools').is_dir() and (p / 'trabajos').is_dir())
ENTRADAS = ROOT / 'trabajos/ganttier_ejemplos_20261005/entradas'
OM = next(ENTRADAS.glob('OM-WBS_3PMG_*.xlsx'))
ECO = next(ENTRADAS.glob('Form_Eco_E1_E2_*.xlsx'))

def test_detectar_formatos_y_desconocido(tmp_path):
    assert detectar(OM)[0]['formato'] == 'om_wbs'
    assert detectar(ECO)[0]['formato'] == 'e2_mp'
    path = tmp_path / 'otro.xlsx'
    book = openpyxl.Workbook(); book.active.append(['Nombre', 'Valor']); book.save(path)
    assert detectar(path) == []

def test_om_real_y_faltantes():
    draft = previsualizar(OM, supuestos={'fecha_inicio': '2026-11-01', 'fecha_fin': '2027-10-31'})
    assert draft.resumen['items'] == 175
    assert len(draft.maestras) == 523
    assert draft.resumen['ocurrencias'] == 28717
    assert {s['codigo'] for s in draft.sitios} == {'SDN', 'AND', 'DST'}
    assert {'hh_frecuencia', 'dotacion', 'calendario', 'equipos', 'lotes'} <= {f['clave'] for f in draft.faltantes}
    assert any(a['codigo'] == 'frecuencia_meses' for a in draft.advertencias)
    assert all(t['origen']['fila'] and t['origen']['celdas'] for t in draft.maestras)

def test_e2_mp_real():
    draft = previsualizar(ECO, supuestos={'fecha_inicio': '2026-01-01', 'fecha_fin': '2028-12-31'})
    assert draft.resumen['ocurrencias'] == 1596
    assert draft.resumen['ocurrencias_por_anio'] == {'2026': 498, '2027': 500, '2028': 598}
    assert Decimal(str(draft.resumen['costo_total'])) == Decimal('12824.875')
    assert len(draft.excluidos) == 2
    assert any(a['codigo'] == 'diferencia_resumen' and a['diferencia'] == -720 for a in draft.advertencias)

def test_perfiles_ida_y_vuelta_versionados(tmp_path):
    store = AlmacenPerfiles(tmp_path)
    profile = Perfil.model_validate(detectar(OM)[0]['perfil'])
    profile.id = 'mi-om'; profile.nombre = 'Mi matriz'
    saved = store.guardar(profile)
    assert saved.version == 1
    saved.nombre = 'Mi matriz revisada'
    assert store.guardar(saved).version == 2
    assert AlmacenPerfiles(tmp_path).obtener('mi-om').nombre == 'Mi matriz revisada'
    store.borrar('mi-om')
    assert all(p.id != 'mi-om' for p in store.listar())

def test_variante_om_columnas_anio_y_sitio(tmp_path):
    book = openpyxl.load_workbook(OM)
    ws = book['OM-WBS']
    ws['D5'] = None
    for row in ws:
        for cell in row:
            if isinstance(cell.value, str) and 'DST' in cell.value:
                cell.value = '/'.join(x for x in cell.value.split('/') if x != 'DST') or None
    ws['J5'] = 2030
    # openpyxl no desplaza rangos combinados al insertar: descombinar evita
    # destruir valores al guardar la variante (no es un comportamiento del lector).
    for merged in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged))
    ws.insert_cols(1, 2)
    path = tmp_path / 'variante.xlsx'; book.save(path)
    draft = previsualizar(path)
    assert len(draft.maestras) == 349
    assert {s['codigo'] for s in draft.sitios} == {'SDN', 'AND'}
    assert draft.supuestos['fecha_inicio'] == '2030-11-01'
    assert draft.perfil.mapeo['codigo'] == 'C'

def test_variante_eco_columnas_y_mayusculas(tmp_path):
    book = openpyxl.load_workbook(ECO)
    ws = book['E2-MP']; ws['B11'] = 'EQUÍPO'; ws['C11'] = 'ACTIVIDAD'
    for merged in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged))
    ws.insert_cols(1, 3)
    path = tmp_path / 'eco_desplazado.xlsx'; book.save(path)
    draft = previsualizar(path, supuestos={'fecha_inicio': '2030-01-01', 'fecha_fin': '2032-12-31'})
    assert draft.resumen['ocurrencias'] == 1596
    assert draft.resumen['costo_total'] == 12824.875
    assert set(draft.resumen['ocurrencias_por_anio']) == {'2030', '2031', '2032'}


def test_no_elegir_copia_auxiliar_con_encabezados(tmp_path):
    book=openpyxl.load_workbook(ECO)
    ws=book['E2-MP']
    for origin,target in zip(range(2,9),range(12,19)):
        ws.cell(11,target,ws.cell(11,origin).value)
    path=tmp_path/'con_copia_auxiliar.xlsx';book.save(path)
    detected=detectar(path)[0]['perfil']
    assert detected['mapeo']['equipo']=='B' and detected['mapeo']['precio']=='D'
    assert detected['mapeo']['cantidades']==['E','F','G']


def test_variante_om_otro_mes_de_inicio(tmp_path):
    book=openpyxl.load_workbook(OM); ws=book['OM-WBS']; ws['J5']=2030
    for c,month in enumerate(['May','Jun','Jul','Ago','Sept','Oct','Nov','Dic','Ene','Feb','Mar','Abr'],10):
        ws.cell(6,c,month)
    path=tmp_path/'inicio_mayo.xlsx';book.save(path)
    draft=previsualizar(path)
    assert len(draft.maestras)==523
    assert draft.supuestos['fecha_inicio']=='2030-05-01' and draft.supuestos['fecha_fin']=='2031-04-30'
    master=next(t for t in draft.maestras if t['code']=='OM-1.1.1-SDN')
    assert master['first_due']=='2030-05-15' and master['preferred_months']=='5,6,7,8,9,10,11,12,1,2,3,4'


def test_variante_om_una_planta_mas(tmp_path):
    book=openpyxl.load_workbook(OM);ws=book['OM-WBS']
    for merged in list(ws.merged_cells.ranges):ws.unmerge_cells(str(merged))
    ws.insert_rows(6);ws['D6']='Planta adicional [NUEVO]'
    for row in ws.iter_rows(min_row=8,min_col=10,max_col=21):
        for cell in row:
            if cell.value:cell.value=str(cell.value)+'/NUEVO'
    path=tmp_path/'cuatro_plantas.xlsx';book.save(path)
    draft=previsualizar(path)
    assert len(draft.maestras)==698
    assert {s['codigo'] for s in draft.sitios}=={'SDN','AND','DST','NUEVO'}
    assert not draft.tiene_errores
