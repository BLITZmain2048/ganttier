"""Asistente de cuatro pasos; toda lectura y creación pasa por la API pública."""
from __future__ import annotations

from .widgets.responsive import ResponsiveDialog
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QCheckBox, QHeaderView, QAbstractItemView, QInputDialog, QDoubleSpinBox)
from .widgets.common import button, run_task


ETIQUETAS = {
    'codigo': 'Código', 'descripcion': 'Descripción / actividad', 'descripciones_wbs': 'Columnas de títulos WBS',
    'frecuencias': 'Frecuencias → columna', 'meses': 'Meses, años y columnas', 'sitios': 'Plantas / sitios',
    'anio_en_fuente': 'Año presente en la fuente', 'filas_ignorar': 'Filas que se omiten',
    'palabras_ignorar': 'Prefijos de subtotales', 'columnas_auxiliares': 'Columnas auxiliares (no importar)',
    'equipo': 'Equipo físico', 'precio': 'Precio unitario', 'cantidades': 'Cantidades anuales',
    'total': 'Total de fila', 'subtotales': 'Subtotales de control', 'detener_en_total': 'Terminar en total del detalle',
}


def texto(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _table(headers, editable=False):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True); table.verticalHeader().hide()
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.horizontalHeader().setStretchLastSection(True)
    if not editable: table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    return table


class ImportadorAsistido(ResponsiveDialog):
    """Ventana modal no bloqueante durante operaciones de archivo y API."""
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle('Importar archivo de cliente')
        self.resize(1060, 760)
        self.paso = 0; self.ocupado = False; self.archivo = None
        self.carga = None; self.borrador = None; self.resultado = None
        self.perfiles = []; self._mapping_types = {}; self._missing_types = {}
        root = QVBoxLayout(self)
        title = QLabel('Importador asistido con perfiles'); title.setProperty('role', 'title')
        root.addWidget(title)
        self.progreso = QLabel(); self.progreso.setProperty('role', 'caption'); root.addWidget(self.progreso)
        self.paginas = QStackedWidget(); root.addWidget(self.paginas, 1)
        self._archivo_page(); self._mapeo_page(); self._faltantes_page(); self._resumen_page()
        self.estado = QLabel('Seleccione el archivo original del cliente.'); self.estado.setTextFormat(Qt.PlainText)
        self.estado.setWordWrap(True); root.addWidget(self.estado)
        actions = QHBoxLayout(); root.addLayout(actions)
        self.cancelar = button('Cancelar', self.reject)
        self.anterior = button('Atrás', self.atras)
        self.siguiente = button('Detectar formato', self.avanzar, primary=True)
        actions.addWidget(self.cancelar); actions.addStretch(); actions.addWidget(self.anterior); actions.addWidget(self.siguiente)
        self._controls()

    def _page(self, title, subtitle):
        page = QWidget(); layout = QVBoxLayout(page)
        heading = QLabel(title); heading.setProperty('role', 'section'); layout.addWidget(heading)
        caption = QLabel(subtitle); caption.setWordWrap(True); caption.setProperty('role', 'caption'); layout.addWidget(caption)
        self.paginas.addWidget(page)
        return layout

    def _archivo_page(self):
        layout = self._page('1 · Archivo', 'El archivo original permanece intacto. El asistente creará un proyecto nuevo.')
        self.ruta = QLineEdit(); self.ruta.setReadOnly(True); self.ruta.setPlaceholderText('Excel .xlsx, caso .json o inventario .pdf')
        row = QHBoxLayout(); row.addWidget(self.ruta, 1); row.addWidget(button('Elegir archivo…', self.elegir_archivo)); layout.addLayout(row)
        hint = QLabel('Formatos incluidos: Matriz OM-WBS, Formulario E-2-MP, Caso JSON de proyecto e Inventario de equipos.\n'
                      'Revisará las columnas, los datos faltantes y el resumen antes de confirmar.\n'
                      'Los valores que no provengan de la fuente quedarán marcados como SUPUESTO.')
        hint.setWordWrap(True); layout.addWidget(hint)
        layout.addWidget(button('Descargar plantilla Excel de inventario…', self.descargar_plantilla_inventario)); layout.addStretch()

    def _mapeo_page(self):
        layout = self._page('2 · Formato y mapeo', 'Revise hoja, encabezado y columnas. Doble clic en un valor estructurado abre su editor.')
        form = QFormLayout(); layout.addLayout(form)
        self.selector_perfil = QComboBox(); self.selector_perfil.currentIndexChanged.connect(self._seleccionar_perfil)
        form.addRow('Perfil', self.selector_perfil)
        self.hoja = QLineEdit(); self.fila = QSpinBox(); self.fila.setRange(1,1000)
        row = QHBoxLayout(); row.addWidget(self.hoja, 1); row.addWidget(QLabel('Fila de encabezado')); row.addWidget(self.fila)
        form.addRow('Hoja', row)
        self.deteccion = QLabel(); self.deteccion.setWordWrap(True); layout.addWidget(self.deteccion)
        self.mapeo = _table(['Campo', 'Valor editable'], True); self.mapeo.setColumnWidth(0, 290)
        self.mapeo.cellDoubleClicked.connect(lambda r,c: self._editar_estructura(self.mapeo, r, c, self._mapping_types))
        layout.addWidget(self.mapeo, 2)
        row = QHBoxLayout(); row.addWidget(button('Actualizar vista previa', self.actualizar_vista)); row.addWidget(button('Guardar perfil como…', self.guardar_perfil)); row.addWidget(button('Actualizar perfil', self.actualizar_perfil))
        row.addWidget(button('Borrar perfil de usuario', self.borrar_perfil)); row.addStretch(); layout.addLayout(row)
        layout.addWidget(QLabel('Vista previa de filas de origen (solo lectura)'))
        self.vista = _table([]); layout.addWidget(self.vista, 1)

    def _faltantes_page(self):
        layout = self._page('3 · Datos faltantes y supuestos', 'Edite los valores propuestos. Las HH son totales por ejecución, no por persona. '
                            'Equipos, rutas y lotes son opcionales. Los registros condicionales quedan pendientes sin programar.')
        self.faltantes = _table(['Dato', 'Valor editable', 'Motivo / procedencia'], True)
        self.faltantes.setColumnWidth(0, 250); self.faltantes.setColumnWidth(1, 350)
        self.faltantes.cellDoubleClicked.connect(lambda r,c: self._editar_estructura(self.faltantes, r, c, self._missing_types))
        layout.addWidget(self.faltantes, 3)
        actions = QHBoxLayout()
        self.revisar_inventario_btn = button('Revisar / corregir inventario…', self.revisar_inventario)
        self.vincular_inventario_btn = button('Vincular cantidad a tarea o ruta…', self.vincular_inventario)
        self.exportar_inventario_btn = button('Descargar inventario para revisión…', self.descargar_inventario)
        for b in (self.revisar_inventario_btn, self.vincular_inventario_btn, self.exportar_inventario_btn):
            b.setEnabled(False); actions.addWidget(b)
        layout.addLayout(actions)
        self.advertencias = QPlainTextEdit(); self.advertencias.setReadOnly(True)
        layout.addWidget(QLabel('Advertencias de la fuente')); layout.addWidget(self.advertencias, 1)

    def _resumen_page(self):
        layout = self._page('4 · Resumen y confirmación', 'El proyecto conservará la fuente, las filas/celdas de origen, los supuestos y las advertencias.')
        form = QFormLayout(); self.nombre = QLineEdit(); form.addRow('Nombre del proyecto', self.nombre); layout.addLayout(form)
        self.resumen = QPlainTextEdit(); self.resumen.setReadOnly(True); layout.addWidget(self.resumen, 1)
        self.acepta_supuestos = QCheckBox('He revisado los SUPUESTOS de planificación y acepto guardarlos con el proyecto.')
        self.acepta_advertencias = QCheckBox('He revisado las advertencias y las partidas que quedarán sin programar.')
        self.acepta_inventario = QCheckBox('He contrastado las filas y los totales del inventario extraído con el PDF original.')
        self.acepta_inventario.setVisible(False)
        for check in (self.acepta_supuestos, self.acepta_advertencias, self.acepta_inventario):
            check.toggled.connect(lambda *_: self._controls()); layout.addWidget(check)

    def set_source(self, path):
        self.archivo = Path(path); self.ruta.setText(str(path)); self.carga = None; self.borrador = None
        self.paso = 0; self.paginas.setCurrentIndex(0); self._controls()

    def _descargar_excel(self, url, filename):
        path, _ = QFileDialog.getSaveFileName(self, 'Guardar Excel', filename, 'Libro Excel (*.xlsx)')
        if not path: return
        def done(content):
            Path(path).write_bytes(content); self.estado.setText('Excel guardado: '+path)
        self._work(lambda: self.client.get(url, raw=True), done, 'Preparando Excel…')

    def descargar_plantilla_inventario(self):
        self._descargar_excel('/importadores/plantillas/inventario.xlsx', 'Plantilla_inventario.xlsx')

    def descargar_inventario(self):
        if self.carga:
            self._descargar_excel(f"/importadores/borradores/{self.carga['id']}/inventario.xlsx", 'Inventario_para_revision.xlsx')

    def _set_supuesto(self, key, value):
        for row in range(self.faltantes.rowCount()):
            if self.faltantes.item(row, 0).data(Qt.UserRole) == key:
                self.faltantes.item(row, 1).setText(texto(value))
                self.acepta_inventario.setChecked(False)
                return
        raise ValueError('El perfil seleccionado no permite '+key)

    def revisar_inventario(self):
        if not self.borrador: return
        from ..importadores.inventario import COLUMNAS, validar_filas
        try:
            supplied=self._leer_tabla(self.faltantes,self._missing_types)
            rows=validar_filas(supplied.get('filas_revisadas') or self.borrador['modelo'].get('inventario_base', self.borrador['modelo']['inventario']))
        except ValueError as exc: self._failed(exc); return
        dialog=ResponsiveDialog(self); dialog.setWindowTitle('Revisar inventario por planta y bloque'); dialog.resize(1150,650)
        layout=QVBoxLayout(dialog)
        caption=QLabel('Contraste Cantidad y Detalle con la fuente. Los totales de control están excluidos. Strings e hileras derivados se recalculan desde sus parámetros. '
                       'Puede corregir o retirar filas; los cambios se validan al preparar el resumen.')
        caption.setWordWrap(True); layout.addWidget(caption)
        table=_table(list(COLUMNAS.values()),True); layout.addWidget(table,1)
        table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,key in enumerate(COLUMNAS): table.setItem(i,j,QTableWidgetItem(str(r.get(key,''))))
        table.resizeColumnsToContents()
        row=QHBoxLayout(); layout.addLayout(row)
        def add():
            i=table.rowCount(); table.insertRow(i)
            for j in range(len(COLUMNAS)): table.setItem(i,j,QTableWidgetItem(''))
        def remove():
            for i in sorted({x.row() for x in table.selectedIndexes()},reverse=True): table.removeRow(i)
        row.addWidget(button('Agregar fila',add)); row.addWidget(button('Retirar filas seleccionadas',remove))
        status=QLabel(); status.setWordWrap(True); layout.addWidget(status)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.Save).setText('Guardar')
        buttons.button(QDialogButtonBox.Cancel).setText('Cancelar')
        def save():
            try:
                values=[{k:table.item(i,j).text() for j,k in enumerate(COLUMNAS)} for i in range(table.rowCount())]
                if not values: raise ValueError('El inventario revisado no puede quedar vacío; cancele la importación si desea retirarlo completo.')
                for r in values: r['cantidad']=int(r['cantidad'])
                self._set_supuesto('filas_revisadas',validar_filas(values)); dialog.accept()
                self.estado.setText('Correcciones preparadas. Revise los parámetros derivados y prepare un nuevo resumen.')
            except (ValueError,AttributeError) as exc: status.setText(str(exc))
        buttons.accepted.connect(save); buttons.rejected.connect(dialog.reject); dialog.exec()

    def vincular_inventario(self):
        if not self.borrador: return
        try:
            supplied=self._leer_tabla(self.faltantes,self._missing_types)
            targets=[('actividad',m['code'],m.get('name',m['code'])) for m in supplied.get('maestras',[])]+[('ruta',r['code'],r.get('name',r['code'])) for r in supplied.get('rutas',[])]
        except (ValueError,KeyError,TypeError) as exc:
            self._failed(ValueError('Revise la estructura de Tareas maestras y Rutas: '+str(exc))); return
        if not targets:
            self.estado.setText('Defina primero Tareas maestras o Rutas en la tabla de supuestos; luego podrá seleccionar su objeto y regla de cantidad.'); return
        from ..importadores.inventario import derivar
        try:
            rows=derivar(supplied.get('filas_revisadas') or self.borrador['modelo'].get('inventario_base',self.borrador['modelo']['inventario']),
                         supplied.get('strings_por_SCB'),supplied.get('hileras_por_tracker'))
        except ValueError as exc: self._failed(exc); return
        dialog=ResponsiveDialog(self); dialog.setWindowTitle('Cantidad del inventario para tarea o ruta'); dialog.resize(650,400)
        layout=QVBoxLayout(dialog); form=QFormLayout(); layout.addLayout(form)
        target=QComboBox()
        for kind,code,name in targets: target.addItem(f'{code} · {name} ({kind})',(kind,code))
        plant=QComboBox(); plant.addItems(sorted({r['planta'] for r in rows}))
        obj=QComboBox(); block=QComboBox(); rule=QComboBox()
        for label,value in [('100 % del inventario','total'),('Porcentaje por bloque, redondeado hacia arriba','porcentaje_por_bloque'),('Cantidad fija','fija')]: rule.addItem(label,value)
        percentage=QDoubleSpinBox(); percentage.setRange(0,100); percentage.setValue(15); percentage.setSuffix(' %')
        fixed=QSpinBox(); fixed.setRange(0,1000000000); fixed.setValue(1)
        for name,widget in [('destino',target),('planta',plant),('objeto',obj),('bloque',block),('regla',rule),('porcentaje',percentage),('cantidad',fixed)]:
            widget.setObjectName('vinculo_'+name)
        def objects():
            obj.clear(); obj.addItems(sorted({r['objeto'] for r in rows if r['planta']==plant.currentText()}))
        def blocks():
            block.clear(); block.addItem('Todos los bloques','')
            for value in sorted({r['bloque'] for r in rows if r['planta']==plant.currentText() and r['objeto']==obj.currentText()}): block.addItem(value,value)
        plant.currentTextChanged.connect(objects); obj.currentTextChanged.connect(blocks); objects()
        for label,widget in [('Actividad o ruta',target),('Planta',plant),('Objeto',obj),('Bloque',block),('Regla',rule),('Porcentaje',percentage),('Cantidad fija',fixed)]: form.addRow(label,widget)
        hint=QLabel('El vínculo sustituye Cantidad_Total; conserve tamaño de lote, rendimiento y equipos en Lotes/Rutas.'); hint.setWordWrap(True); layout.addWidget(hint)
        status=QLabel(); status.setWordWrap(True); layout.addWidget(status)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.Save).setText('Guardar')
        buttons.button(QDialogButtonBox.Cancel).setText('Cancelar')
        def link_value():
            kind,code=target.currentData()
            link=dict(codigo=code,destino=kind,planta=plant.currentText(),objeto=obj.currentText(),bloque=block.currentData(),regla=rule.currentData())
            if link['regla']=='porcentaje_por_bloque': link['porcentaje']=percentage.value()
            if link['regla']=='fija': link['cantidad']=fixed.value()
            return link
        def calculate():
            from ..importadores.inventario import calcular_cantidad
            percentage.setEnabled(rule.currentData()=='porcentaje_por_bloque')
            fixed.setEnabled(rule.currentData()=='fija')
            try:
                quantity=calcular_cantidad(rows,link_value())
                status.setText(f'Cantidad total resultante: {quantity:,}'.replace(',','.'))
                buttons.button(QDialogButtonBox.Save).setEnabled(quantity>0)
            except (ValueError,TypeError) as exc:
                status.setText(str(exc)); buttons.button(QDialogButtonBox.Save).setEnabled(False)
        def restore():
            kind,code=target.currentData()
            previous=next((v for v in supplied.get('vinculos',[]) if (v.get('destino','actividad'),v['codigo'])==(kind,code)),None)
            if previous:
                plant.setCurrentText(previous['planta']); obj.setCurrentText(previous['objeto'])
                block.setCurrentIndex(max(0,block.findData(previous.get('bloque',''))))
                rule.setCurrentIndex(max(0,rule.findData(previous['regla'])))
                percentage.setValue(previous.get('porcentaje',15)); fixed.setValue(previous.get('cantidad',1))
            calculate()
        target.currentIndexChanged.connect(restore)
        for combo in (plant,obj,block,rule): combo.currentIndexChanged.connect(calculate)
        percentage.valueChanged.connect(calculate); fixed.valueChanged.connect(calculate); restore()
        def save():
            from ..importadores.inventario import calcular_cantidad
            try:
                kind,code=target.currentData(); link=link_value()
                quantity=calcular_cantidad(rows,link)
                links=[v for v in supplied.get('vinculos',[]) if (v.get('destino','actividad'),v['codigo'])!=(kind,code)]
                self._set_supuesto('vinculos',links+[link]); dialog.accept()
                self.estado.setText(f'{code}: cantidad calculada {quantity}. Prepare el resumen para validar lotes y rutas.')
            except (ValueError,TypeError) as exc: status.setText(str(exc))
        buttons.accepted.connect(save); buttons.rejected.connect(dialog.reject); dialog.exec()

    def elegir_archivo(self):
        def choose(formats):
            extensions = sorted({ext for entry in formats for ext in entry['extensiones']})
            filters = 'Formatos admitidos ('+' '.join('*'+ext for ext in extensions)+')'
            path, _ = QFileDialog.getOpenFileName(self, 'Seleccionar archivo de cliente', '', filters)
            if path: self.set_source(path)
        self._work(lambda: self.client.get('/importadores/formatos'), choose, 'Consultando formatos instalados…')

    def _controls(self):
        titles = ['Detectar formato', 'Revisar datos faltantes', 'Preparar resumen', 'Crear proyecto']
        self.progreso.setText('  →  '.join(('● ' if i == self.paso else '')+label for i,label in enumerate(['Archivo', 'Mapeo', 'Datos faltantes', 'Confirmación'])))
        self.siguiente.setText(titles[self.paso]); self.anterior.setEnabled(self.paso > 0 and not self.ocupado)
        allowed = self.archivo is not None
        if self.paso == 3:
            allowed = bool(self.borrador and self.acepta_supuestos.isChecked() and self.acepta_advertencias.isChecked()
                           and (not self.borrador.get('modelo', {}).get('requiere_revision') or self.acepta_inventario.isChecked())
                           and not any(w.get('nivel') == 'error' for w in self.borrador['advertencias']))
        self.siguiente.setEnabled(allowed and not self.ocupado)
        self.cancelar.setEnabled(not self.ocupado)
        self.paginas.setEnabled(not self.ocupado)

    def _work(self, operation, done, message):
        self.ocupado = True; self.estado.setText(message); self._controls()
        def success(value):
            self.ocupado = False
            try: done(value)
            except Exception as exc: self._failed(exc)
            self._controls()
        run_task(operation, success, self._failed)

    def _failed(self, exc):
        self.ocupado = False
        self.estado.setText(str(getattr(exc, 'message', exc)))
        self._controls()

    def _ir(self, step):
        self.paso = step; self.paginas.setCurrentIndex(step); self._controls()

    def atras(self):
        if not self.ocupado:
            self.acepta_supuestos.setChecked(False); self.acepta_advertencias.setChecked(False)
            self.acepta_inventario.setChecked(False)
            self._ir(max(0, self.paso-1))

    def avanzar(self):
        if self.ocupado: return
        try:
            if self.paso == 0: self._subir()
            elif self.paso == 1: self._previsualizar(False)
            elif self.paso == 2: self._previsualizar(True)
            elif self.paso == 3: self._confirmar()
        except Exception as exc:
            self._failed(exc)

    def _subir(self):
        path = self.archivo
        if not path or not path.is_file(): raise ValueError('Seleccione un archivo existente.')
        def work():
            with path.open('rb') as handle:
                upload = self.client.request('POST', '/importadores/archivos', files={'file': (path.name, handle)})
            profiles = self.client.get('/importadores/perfiles')
            return upload, profiles
        def done(result):
            self.carga, profiles = result
            detections = self.carga['detecciones']
            detected = [d['perfil'] for d in detections]
            self.perfiles = detected + [p for p in profiles if p['id'] not in {d['id'] for d in detected}]
            self.selector_perfil.blockSignals(True); self.selector_perfil.clear()
            for p in self.perfiles: self.selector_perfil.addItem(f"{p['nombre']} · v{p['version']}")
            self.selector_perfil.blockSignals(False)
            self.deteccion.setText('\n'.join(f"{d['perfil']['nombre']}: confianza {d['puntaje']:.0%} · {', '.join(d['motivos'])}" for d in detections) or
                                   'Formato no reconocido: seleccione un perfil y revise su mapeo.')
            self._seleccionar_perfil(0); self._ir(1)
            self.estado.setText('Archivo cargado. Revise el mapeo.')
            if detections:
                self._work(lambda: self.client.post(f"/importadores/borradores/{self.carga['id']}", {'perfil': detected[0]}),
                           self._mostrar_vista, 'Leyendo vista previa…')
        self._work(work, done, 'Detectando formato…')

    def _seleccionar_perfil(self, index):
        if not 0 <= index < len(self.perfiles): return
        profile = self.perfiles[index]
        self.hoja.setText(profile.get('hoja', '')); self.fila.setValue(profile.get('fila_encabezado', 1))
        self._llenar_mapeo(profile.get('mapeo', {}))

    def _llenar_mapeo(self, mapping):
        self.mapeo.setRowCount(0); self._mapping_types = {}
        for key, value in mapping.items():
            row = self.mapeo.rowCount(); self.mapeo.insertRow(row)
            item = QTableWidgetItem(ETIQUETAS.get(key, key)); item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setToolTip(item.text())
            cell = QTableWidgetItem(texto(value)); cell.setToolTip(texto(value))
            self.mapeo.setItem(row, 0, item); self.mapeo.setItem(row, 1, cell)
            self._mapping_types[key] = type(value)

    def _leer_tabla(self, table, types):
        result = {}
        for row in range(table.rowCount()):
            key = table.item(row, 0).data(Qt.UserRole)
            if key not in types: continue
            value = table.item(row, 1).text()
            try: result[key] = value if types[key] is str else json.loads(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f'Valor inválido en {table.item(row,0).text()}: revise números, listas o JSON.') from exc
        return result

    def _perfil(self):
        index = self.selector_perfil.currentIndex()
        if not 0 <= index < len(self.perfiles): raise ValueError('Seleccione un perfil.')
        return {**self.perfiles[index], 'hoja': self.hoja.text().strip(), 'fila_encabezado': self.fila.value(),
                'mapeo': self._leer_tabla(self.mapeo, self._mapping_types)}

    def _mostrar_vista(self, draft):
        self.borrador = draft
        inventory = 'inventario' in draft.get('modelo', {})
        for b in (self.revisar_inventario_btn, self.vincular_inventario_btn, self.exportar_inventario_btn):
            b.setEnabled(inventory); b.setVisible(inventory)
        self.acepta_inventario.setVisible(bool(draft.get('modelo', {}).get('requiere_revision')))
        self.acepta_inventario.setChecked(False)
        if not self.mapeo.rowCount():
            self.perfiles[self.selector_perfil.currentIndex()] = draft['perfil']
            self.hoja.setText(draft['perfil']['hoja']); self.fila.setValue(draft['perfil']['fila_encabezado'])
            self._llenar_mapeo(draft['perfil']['mapeo'])
        rows = draft['vista_previa']; columns = list(dict.fromkeys(k for r in rows for k in r['celdas']))
        self.vista.clear(); self.vista.setColumnCount(len(columns)+1); self.vista.setHorizontalHeaderLabels(['Fila']+columns)
        self.vista.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.vista.setItem(i, 0, QTableWidgetItem(str(r['fila'])))
            for j, c in enumerate(columns, 1):
                item = QTableWidgetItem(r['celdas'].get(c, '')); item.setToolTip(item.text()); self.vista.setItem(i, j, item)
        self.vista.resizeColumnsToContents()
        for j in range(self.vista.columnCount()): self.vista.setColumnWidth(j, min(250, max(60, self.vista.columnWidth(j))))
        self.estado.setText(f"Vista previa: {draft['resumen']['items']} ítems; {len(draft['advertencias'])} advertencias/errores.")

    def _previsualizar(self, final):
        body = {'perfil': self._perfil()}
        if final: body['supuestos'] = self._leer_tabla(self.faltantes, self._missing_types)
        def done(draft):
            self._mostrar_vista(draft)
            warning_text = '\n'.join(f"{w.get('nivel', 'advertencia').upper()} · fila {w.get('fila') or '—'}: {w['mensaje']}" for w in draft['advertencias']) or 'Sin advertencias de lectura.'
            self.advertencias.setPlainText(warning_text)
            if not final:
                self.faltantes.setRowCount(0); self._missing_types = {}
                for field in draft['faltantes']:
                    r = self.faltantes.rowCount(); self.faltantes.insertRow(r)
                    label = QTableWidgetItem(field['etiqueta']); label.setData(Qt.UserRole, field['clave'])
                    label.setToolTip(field['etiqueta'])
                    label.setFlags(label.flags() & ~Qt.ItemIsEditable); self.faltantes.setItem(r, 0, label)
                    value = QTableWidgetItem(texto(field['valor'])); value.setToolTip(texto(field['valor'])); self.faltantes.setItem(r, 1, value)
                    reason = QTableWidgetItem(field['estado']+': '+field['motivo']); reason.setFlags(reason.flags() & ~Qt.ItemIsEditable)
                    reason.setToolTip(reason.text())
                    self.faltantes.setItem(r, 2, reason)
                    if field['estado'] == 'PENDIENTE': value.setFlags(value.flags() & ~Qt.ItemIsEditable)
                    else: self._missing_types[field['clave']] = type(field['valor'])
                self._ir(2)
            else:
                self.nombre.setText(self.archivo.stem if not self.nombre.text() else self.nombre.text())
                self.resumen.setPlainText('RESUMEN\n'+json.dumps(draft['resumen'], ensure_ascii=False, indent=2)+
                    '\n\nADVERTENCIAS\n'+warning_text+'\n\nEXCLUSIONES Y TOTALES DE CONTROL\n'+json.dumps(draft.get('excluidos',[]),ensure_ascii=False,indent=2)+
                    '\n\nSUPUESTOS QUE SE GUARDARÁN\n'+json.dumps(draft['supuestos'], ensure_ascii=False, indent=2))
                self.acepta_supuestos.setChecked(False); self.acepta_advertencias.setChecked(False); self._ir(3)
        self._work(lambda: self.client.post(f"/importadores/borradores/{self.carga['id']}", body), done, 'Validando mapeo y calculando borrador…')

    def actualizar_vista(self):
        if not self.carga or self.ocupado: return
        try:
            body = {'perfil': self._perfil()}
            self._work(lambda: self.client.post(f"/importadores/borradores/{self.carga['id']}", body), self._mostrar_vista, 'Actualizando vista previa…')
        except Exception as exc:
            self._failed(exc)

    def _confirmar(self):
        if not self.acepta_supuestos.isChecked() or not self.acepta_advertencias.isChecked():
            raise ValueError('Revise y marque las dos confirmaciones.')
        body = {'revision': self.borrador['revision'], 'nombre': self.nombre.text().strip() or self.archivo.stem,
                'aceptar_supuestos': True, 'aceptar_advertencias': True,
                'aceptar_revision_inventario': self.acepta_inventario.isChecked()}
        def done(result):
            self.resultado = result; self.estado.setText('Proyecto creado con sus supuestos y trazabilidad.'); self.accept()
        self._work(lambda: self.client.post(f"/importadores/confirmar/{self.carga['id']}", body), done, 'Creando proyecto…')

    def _editar_estructura(self, table, row, column, types):
        if column != 1: return
        key = table.item(row,0).data(Qt.UserRole)
        if types.get(key) not in (dict, list): return
        dialog = ResponsiveDialog(self); dialog.setWindowTitle(table.item(row,0).text()); dialog.resize(720,480)
        layout = QVBoxLayout(dialog); layout.addWidget(QLabel('Edite la estructura JSON. Se valida antes de guardar.'))
        editor = QPlainTextEdit()
        try: editor.setPlainText(json.dumps(json.loads(table.item(row,1).text()), ensure_ascii=False, indent=2))
        except ValueError: editor.setPlainText(table.item(row,1).text())
        layout.addWidget(editor); status = QLabel(); layout.addWidget(status)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.Save).setText('Guardar')
        buttons.button(QDialogButtonBox.Cancel).setText('Cancelar')
        def save():
            try:
                value = json.loads(editor.toPlainText())
                if not isinstance(value, types[key]): raise ValueError('Conserve el tipo lista u objeto del valor original.')
                table.item(row,1).setText(texto(value)); dialog.accept()
            except ValueError as exc: status.setText(str(exc))
        buttons.accepted.connect(save); buttons.rejected.connect(dialog.reject); dialog.exec()

    def guardar_perfil(self):
        name, ok = QInputDialog.getText(self, 'Guardar perfil', 'Nombre del perfil reutilizable')
        if not ok or not name.strip(): return
        from uuid import uuid4
        profile = self._perfil(); profile.update(id='usuario-'+uuid4().hex[:12], nombre=name.strip(), version=1)
        self._guardar(profile)

    def actualizar_perfil(self):
        try: self._guardar(self._perfil())
        except Exception as exc: self._failed(exc)

    def _guardar(self, profile):
        if self.faltantes.rowCount() and self.borrador and self.borrador['perfil']['formato'] == profile['formato']:
            profile['supuestos'] = self._leer_tabla(self.faltantes, self._missing_types)
        def done(saved):
            index = next((i for i,p in enumerate(self.perfiles) if p['id'] == saved['id']), None)
            if index is None:
                self.perfiles.append(saved); self.selector_perfil.addItem(f"{saved['nombre']} · v{saved['version']}"); index=len(self.perfiles)-1
            else:
                self.perfiles[index] = saved; self.selector_perfil.setItemText(index, f"{saved['nombre']} · v{saved['version']}")
            self.selector_perfil.setCurrentIndex(index); self.estado.setText('Perfil guardado para próximas importaciones.')
        self._work(lambda: self.client.post('/importadores/perfiles', profile), done, 'Guardando perfil…')

    def borrar_perfil(self):
        index = self.selector_perfil.currentIndex()
        if index < 0: return
        ident = self.perfiles[index]['id']
        def done(_):
            self.perfiles.pop(index); self.selector_perfil.removeItem(index); self.estado.setText('Perfil de usuario eliminado.')
        self._work(lambda: self.client.delete(f'/importadores/perfiles/{ident}'), done, 'Eliminando perfil…')

    def reject(self):
        if self.ocupado: return
        if self.carga and not self.resultado:
            ident = self.carga['id']
            self.carga = None
            self._work(lambda: self.client.delete(f'/importadores/archivos/{ident}'),
                       lambda _: QDialog.reject(self), 'Eliminando la carga temporal…')
        else:
            super().reject()


def abrir_importador(state, parent=None):
    dialog = ImportadorAsistido(state.client, parent)
    if dialog.exec() == QDialog.Accepted and dialog.resultado:
        return dialog.resultado['proyecto']['id']
    return None
