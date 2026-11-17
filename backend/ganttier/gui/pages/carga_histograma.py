"""Carga agregada y detalle de actividades a demanda."""
from calendar import monthrange
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QGridLayout, QSpinBox, QTableView

from .tablero_trabajadores import AsyncPage, SummaryModel
from .performance import choices, date_editor
from ..widgets.graficos import CurvaSWidget


def period_end(start, period):
    day = date.fromisoformat(start)
    if period == "week":
        day += timedelta(days=6)
    elif period == "month":
        day = day.replace(day=monthrange(day.year, day.month)[1])
    return day.isoformat()


def period_activities(client, pid, group, key, start, end):
    """Lee por lotes y filtra asignaciones con la misma clave que load_profile."""
    resources = {r["id"]: r for r in client.resources(pid)}
    assigned = set()
    by_activity = {}
    for a in client.assignments(pid):
        assigned.add(a["activity_id"])
        by_activity.setdefault(a["activity_id"], []).append(a)
    result, offset = [], 0
    while True:
        rows = client.activities(pid, **{"from": start, "to": end, "limit": 1000, "offset": offset})
        for row in rows:
            keys = set()
            for a in by_activity.get(row["id"], []):
                resource = resources.get(a.get("resource_id"))
                if group == "resource":
                    name = resource["name"] if resource else a.get("skill") or row.get("required_skill") or "Sin asignar"
                else:
                    name = a.get("skill") or row.get("required_skill") or ((resource.get("skills") or ["Sin habilidad"])[0] if resource else "Sin habilidad")
                keys.add(name)
            if row["id"] not in assigned:
                keys.add(row.get("required_skill") or "Sin asignar")
            if key in keys:
                result.append({k: row.get(k) for k in ("code", "name", "planned_start", "planned_finish")})
        offset += len(rows)
        if len(rows) < 1000:
            return result


class CargaHistogramaPage(AsyncPage):
    title = "Histograma de carga"
    subtitle = "Carga y capacidad por recurso o habilidad"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.profile = None
        self.details = []
        today = (state.project or {}).get("data_date") or date.today()
        self.start, self.end = date_editor(today, "Desde"), date_editor(today, "Hasta")
        self.group = choices([("resource", "Recurso"), ("skill", "Habilidad")], "Agrupar por")
        self.period = choices([("day", "Día"), ("week", "Semana"), ("month", "Mes")], "Periodo")
        self.series = choices([], "Recurso o habilidad")
        self.chart_page = QSpinBox()
        self.chart_page.setRange(1, 1)
        self.chart_page.setAccessibleName("Bloque de hasta 30 periodos")
        controls = QGridLayout()
        for col, (label, widget) in enumerate((("Desde", self.start), ("Hasta", self.end), ("Agrupar", self.group), ("Periodo", self.period))):
            controls.addWidget(QLabel(label), 0, col)
            controls.addWidget(widget, 1, col)
        controls.addWidget(self.series, 2, 0, 1, 2)
        controls.addWidget(self.chart_page, 2, 2)
        refresh = QPushButton("Actualizar")
        refresh.clicked.connect(self.refresh)
        controls.addWidget(refresh, 2, 3)
        self.root.addLayout(controls)
        self.root.addWidget(self.status)
        self.chart = CurvaSWidget(histogram=True)
        self.root.addWidget(self.chart, 2)
        note = QLabel("Selecciona una barra o pulsa Enter para abrir las actividades del periodo. "
                      "Capacidad del servidor: las ausencias parciales pueden sobreestimarla. "
                      "Las habilidades pueden compartir personas; no sumes sus capacidades.")
        note.setWordWrap(True)
        self.root.addWidget(note)
        self.detail_label = QLabel("Actividades del periodo seleccionado")
        self.root.addWidget(self.detail_label)
        self.detail_model = SummaryModel([(k, v) for k, v in (("code", "Código"), ("name", "Actividad"),
                                        ("planned_start", "Inicio"), ("planned_finish", "Fin"))])
        self.table = QTableView()
        self.table.setModel(self.detail_model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 360)
        self.root.addWidget(self.table, 1)
        self.detail_page = QSpinBox()
        self.detail_page.setRange(1, 1)
        self.detail_page.setAccessibleName("Página de actividades, 200 por página")
        self.root.addWidget(self.detail_page)
        self.series.currentIndexChanged.connect(self.draw)
        self.chart_page.valueChanged.connect(self.draw)
        self.detail_page.valueChanged.connect(self.show_details)
        self.chart.period_activated.connect(self.open_period)
        for field in (self.start, self.end):
            field.dateChanged.connect(self.invalidate)
        for field in (self.group, self.period):
            field.currentIndexChanged.connect(self.invalidate)

    def clear_data(self):
        self.profile = None
        self.details = []
        if hasattr(self, "chart"):
            self.chart.set_data([], [], [])
            self.detail_model.set_rows([])
            self.status.setText("Usa Actualizar para cargar los filtros.")

    def refresh(self, *_):
        self.invalidate()
        pid, client = self.state.pid, self.state.client
        start, end = self.start.date().toString(Qt.ISODate), self.end.date().toString(Qt.ISODate)
        group, period = self.group.currentData(), self.period.currentData()
        if pid is None or start > end:
            self.status.setText("Abre un proyecto." if pid is None else "Desde debe ser anterior o igual a Hasta.")
            return
        self._query = (pid, group, period, start, end)
        self.submit(lambda: client.load(pid, group=group, period=period, date_from=start, date_to=end), self.loaded)

    def loaded(self, profile):
        self.profile = profile
        self.series.blockSignals(True)
        self.series.clear()
        for series in profile["series"]:
            self.series.addItem(series["key"])
        self.series.blockSignals(False)
        self.chart_page.setRange(1, max(1, (len(profile["labels"])+29)//30))
        self.chart_page.setValue(1)
        self.draw()

    def draw(self, *_):
        self._generation += 1
        self.details = []
        self.detail_model.set_rows([])
        if not self.profile or self.series.currentIndex() < 0:
            return
        series = self.profile["series"][self.series.currentIndex()]
        first = (self.chart_page.value()-1)*30
        labels = self.profile["labels"][first:first+30]
        self.chart.set_data(labels, series["load"][first:first+30], series["capacity"][first:first+30])
        excess = sum(max(0, load-cap) for load, cap in zip(series["load"], series["capacity"]))
        self.status.setText(f"{series['key']} · {excess:,.1f} HH de sobrecarga · bloque {self.chart_page.value()}/{self.chart_page.maximum()}")

    def open_period(self, index):
        if self.profile is None or not self.chart.labels:
            return
        pid, group, period, lower, upper = self._query
        start = max(lower, self.chart.labels[index])
        end = min(upper, period_end(self.chart.labels[index], period))
        key, client = self.series.currentText(), self.state.client
        self.detail_label.setText(f"{key}: {start} a {end}")
        self.submit(lambda: period_activities(client, pid, group, key, start, end), self.details_loaded)

    def details_loaded(self, rows):
        self.details = rows
        self.detail_page.setRange(1, max(1, (len(rows)+199)//200))
        self.detail_page.setValue(1)
        self.show_details()
        self.status.setText(f"{len(rows)} actividades del periodo; páginas de 200 filas.")

    def show_details(self, *_):
        first = (self.detail_page.value()-1)*200
        self.detail_model.set_rows(self.details[first:first+200])
