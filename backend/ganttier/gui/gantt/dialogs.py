"""Activity and WBS forms. All persistence goes through the page's worker queue."""
from __future__ import annotations
import json

from PySide6.QtCore import QObject, Signal
from ..widgets import common
from ..widgets.icons import icon
from ..theme import theme_signals

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTabWidget, QWidget, QTableView,
                               QAbstractItemView, QMessageBox)

from ..widgets.common import Field, FormDialog, Column, DictTableModel, button, hbox, confirm, run_task
from ..widgets.responsive import fit_dialog

CONSTRAINTS = [("ASAP", "Lo antes posible"), ("ALAP", "Lo más tarde posible"),
               ("SNET", "Comenzar no antes de"), ("SNLT", "Comenzar no después de"),
               ("FNET", "Terminar no antes de"), ("FNLT", "Terminar no después de"),
               ("MSO", "Comenzar el"), ("MFO", "Terminar el")]
from .editing import READONLY, validate_activity


def activity_fields(state, wbs, full=True):
    fields = [Field("code", "Código"), Field("name", "Nombre"),
              Field("wbs_id", "WBS", "choice", [(None, "Sin WBS")] + [(w["id"], f"{w['code']} · {w['name']}") for w in wbs]),
              Field("duration_days", "Duración (días laborables)", "int"),
              Field("work_hours", "HH presupuestadas", "float"), Field("macro_month", "Mes macro", "month"),
              Field("required_skill", "Habilidad requerida"), Field("crew_size", "Dotación", "float")]
    if not full:
        return fields
    fields += [Field("cost_budget", "Costo presupuestado", "float"), Field("priority", "Prioridad", "int", maximum=100),
               Field("calendar_id", "Calendario", "choice", [(None, "Del proyecto")] + [(c["id"], c["name"]) for c in state.calendars]),
               Field("constraint_type", "Restricción", "choice", CONSTRAINTS), Field("constraint_date", "Fecha de restricción", "date"),
               Field("window_start", "Ventana desde", "date"), Field("window_end", "Ventana hasta", "date"),
               Field("window_hard", "Ventana obligatoria", "bool"), Field("planned_start", "Inicio programado", "date"),
               Field("planned_finish", "Término programado", "date"), Field("percent_complete", "Avance al corte (%)", "float", maximum=100),
               Field("remaining_duration", "Duración restante (vacío: sin definir)"),
               Field("actual_start", "Inicio real", "date"), Field("actual_finish", "Término real", "date"),
               Field("due_date", "Vencimiento", "date"), Field("location", "Instalación / ubicación"),
               Field("equipment", "Equipo / TAG"), Field("voltage_kv", "Tensión (kV; vacío: sin definir)"),
               Field("discipline", "Disciplina"), Field("requires_outage", "Requiere desconexión", "bool"),
               Field("locked", "Bloqueada para programación", "bool"), Field("notes", "Notas", "multiline", span=True)]
    labels = {"id": "Identificador", "project_id": "Proyecto", "early_start": "Inicio temprano", "early_finish": "Término temprano",
              "late_start": "Inicio tardío", "late_finish": "Término tardío", "total_float": "Holgura total", "free_float": "Holgura libre",
              "critical": "Crítica", "periodic_task_id": "Tarea periódica", "occurrence_index": "Ocurrencia",
              "actual_hours": "HH reales acumuladas", "actual_cost": "Costo real acumulado", "status": "Estado"}
    for key, label in labels.items():
        fields.append(Field(key, label, "date" if key.endswith(("_start", "_finish")) else "bool" if key == "critical" else "text"))
    return fields




def small_table(columns):
    model = DictTableModel(columns)
    table = QTableView()
    table.setModel(model)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.verticalHeader().hide()
    table.horizontalHeader().setStretchLastSection(True)
    return table, model


class WbsDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page, self.pid = page, page.state.pid
        self.setWindowTitle("Administrar WBS")
        self.setModal(True)
        self.table, self.model = small_table([Column("code", "Código"), Column("name", "Nombre"),
                                              Column("parent_name", "Nodo padre"), Column("sort_order", "Orden")])
        root = QVBoxLayout(self)
        root.addLayout(hbox(button("＋ Nodo", self.new_node), button("Editar / mover", self.edit_node),
                            button("Eliminar", self.delete_node), None, button("Cerrar", self.accept)))
        root.addWidget(self.table)
        self.table.doubleClicked.connect(lambda *_: self.edit_node())
        self.set_rows(page.wbs)
        self.resize(820, 480)

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog(self)

    def set_rows(self, rows):
        names = {w["id"]: f"{w['code']} · {w['name']}" for w in rows}
        self.model.set_rows([dict(w, parent_name=names.get(w.get("parent_id"), "Raíz")) for w in rows])

    def selected(self):
        index = self.table.currentIndex()
        return self.model.row_dict(index.row()) if index.isValid() else None

    def form(self, node=None):
        forbidden = {node["id"]} if node else set()
        # Descendants cannot be selected as parents; iterative traversal handles deep trees.
        children = {}
        for w in self.model.rows:
            children.setdefault(w.get("parent_id"), []).append(w["id"])
        stack = list(forbidden)
        while stack:
            for child in children.get(stack.pop(), []):
                if child not in forbidden:
                    forbidden.add(child)
                    stack.append(child)
        choices = [(None, "Raíz")] + [(w["id"], f"{w['code']} · {w['name']}") for w in self.model.rows if w["id"] not in forbidden]
        fields = [Field("code", "Código"), Field("name", "Nombre"), Field("parent_id", "Padre", "choice", choices),
                  Field("sort_order", "Orden", "int", minimum=-1000000)]
        dlg = FormDialog("Editar nodo WBS" if node else "Nuevo nodo WBS", fields, node, self,
                         validate=lambda v: None if v["code"] and v["name"] else "Código y nombre son obligatorios.")
        if dlg.exec():
            self.save_node({f.key: dlg.values()[f.key] for f in fields}, node["id"] if node else None)

    def save_node(self, values, wid=None):
        client, pid = self.page.state.client, self.pid
        def work():
            result = client.update_wbs(wid, values) if wid else client.create_wbs(pid, values)
            return result, client.wbs(pid)
        self.page.mutate(work, lambda result: self.set_rows(result[1]), recalc=False, pid=pid,
                         operation_key=("wbs", wid, json.dumps(values, sort_keys=True)))

    def new_node(self):
        self.form()

    def edit_node(self):
        node = self.selected()
        if node:
            self.form(node)

    def delete_node(self):
        node = self.selected()
        if node and confirm(self, "Eliminar WBS", f"Se eliminará {node['code']} y sus subnodos. Las actividades quedarán sin WBS.", danger=True):
            client, pid = self.page.state.client, self.pid
            def work():
                client.delete_wbs(node["id"])
                return client.wbs(pid)
            self.page.mutate(work, self.set_rows, recalc=False, pid=pid, operation_key=("delete_wbs", node["id"]))


class ActivityDetail(FormDialog):
    def __init__(self, page, activity, relations, assignments):
        self.page, self.original, self.pid = page, dict(activity), page.state.pid
        super().__init__(f"{activity['code']} · {activity['name']}", activity_fields(page.state, page.wbs),
                         activity, page, validate=validate_activity)
        self.setObjectName("ActivityDetail")
        self._saving = False
        for key in READONLY:
            self._editors[key].setEnabled(False)
        self._editors["status"].setText({"no_iniciada": "No iniciada", "en_curso": "En curso", "terminada": "Terminada"}.get(activity.get("status"), ""))
        outer = self.layout()
        scroll = outer.takeAt(0).widget()
        tabs = QTabWidget()
        tabs.addTab(scroll, icon("list-checks"), "Actividad")
        self.rel_table, self.rel_model = small_table([Column("pred_code", "Predecesora"), Column("succ_code", "Sucesora"),
                                                     Column("type", "Tipo"), Column("lag_days", "Desfase (días)")])
        self.asg_table, self.asg_model = small_table([Column("resource_name", "Recurso"), Column("skill", "Habilidad"),
                                                     Column("units", "Unidades"), Column("work_hours", "HH"), Column("actual_hours", "HH reales")])
        self.remove_buttons = {}
        for name, table, add, edit, remove in (("Relaciones", self.rel_table, self.add_relation, self.edit_relation, self.delete_relation),
                                               ("Asignaciones", self.asg_table, self.add_assignment, self.edit_assignment, self.delete_assignment)):
            widget = QWidget()
            layout = QVBoxLayout(widget)
            self.remove_buttons[name] = button("Eliminar", remove, danger=True)
            layout.addLayout(hbox(button("＋ Agregar", add), button("Editar", edit), self.remove_buttons[name]))
            layout.addWidget(table)
            tabs.addTab(widget, icon("git-branch" if name == "Relaciones" else "users"), name)
        outer.insertWidget(0, tabs, 1)
        self.detail_tabs = tabs
        theme_signals.theme_changed.connect(self._refresh_tab_icons)
        self.set_related(relations, assignments)
        self.resize(940, 750)
        # A3-10: el detalle se destruye al cerrarse (si no queda un guardado en curso) para no acumular
        # diálogos ocultos con sus editores y conexiones.
        self.finished.connect(self._dispose_if_idle)

    def _dispose_if_idle(self, *_):
        if not self._saving:
            self.deleteLater()

    def _refresh_tab_icons(self):
        for index, name in enumerate(("list-checks", "git-branch", "users")):
            self.detail_tabs.setTabIcon(index, icon(name))

    def set_related(self, relations, assignments):
        a = self.page.arrays
        def code(aid):
            i = a.id_to_index.get(aid)
            return str(a.codes[i]) if i is not None else f"#{aid}"
        self.rel_model.set_rows([dict(r, pred_code=code(r["pred_id"]), succ_code=code(r["succ_id"])) for r in relations
                                 if self.original["id"] in (r["pred_id"], r["succ_id"])])
        resources = {r["id"]: r["name"] for r in self.page.state.resources}
        self.asg_model.set_rows([dict(a, resource_name=resources.get(a.get("resource_id"), "Genérico")) for a in assignments])

    def reload_related(self, *_):
        client, pid, aid = self.page.state.client, self.pid, self.original["id"]
        run_task(lambda: (client.relations(pid), client.assignments(pid, aid)), lambda result: self.set_related(*result))

    def _accept(self):
        if self._saving:
            return
        values = self.values()
        message = validate_activity(values)
        if message:
            QMessageBox.warning(self, "Revisa los datos", message)
            return
        self._saving = True
        self.setEnabled(False)
        def finish():
            self._saving = False
            if self.isVisible():
                self.setEnabled(True)
            else:
                self.deleteLater()
        self.page.save_detail(self.original, values, self.accept, finish, pid=self.pid)

    @staticmethod
    def selected(table, model):
        index = table.currentIndex()
        return model.row_dict(index.row()) if index.isValid() else None

    def relation_form(self, old=None):
        aid = self.original["id"]
        incoming = old is None or old["succ_id"] == aid
        a = self.page.arrays
        values = {"direction": "pred" if incoming else "succ", "code": "", "type": "FS", "lag_days": 0}
        if old:
            i = a.id_to_index.get(old["pred_id"] if incoming else old["succ_id"])
            values.update(code=str(a.codes[i]) if i is not None else "", type=old["type"], lag_days=old["lag_days"])
        fields = [Field("direction", "Dirección", "choice", [("pred", "Predecesora"), ("succ", "Sucesora")]),
                  Field("code", "Código de la otra actividad"), Field("type", "Tipo", "choice", [(t, t) for t in ("FS", "SS", "FF", "SF")]),
                  Field("lag_days", "Desfase (días laborables)", "int", minimum=-100000)]
        lookup = {str(code): int(aid) for code, aid in zip(a.codes, a.ids)}
        def validate(v):
            if v["code"] not in lookup:
                return "No existe una actividad con ese código."
            if lookup[v["code"]] == aid:
                return "Una actividad no puede depender de sí misma."
        dlg = FormDialog("Editar relación" if old else "Nueva relación", fields, values, self, validate=validate)
        if dlg.exec():
            v = dlg.values()
            other = lookup[v["code"]]
            payload = dict(pred_id=other if v["direction"] == "pred" else aid,
                           succ_id=aid if v["direction"] == "pred" else other, type=v["type"], lag_days=v["lag_days"])
            self.page.save_relation(payload, old, self.reload_related, pid=self.pid)

    def add_relation(self):
        self.relation_form()

    def edit_relation(self):
        row = self.selected(self.rel_table, self.rel_model)
        if row:
            self.relation_form(row)

    def delete_relation(self):
        row = self.selected(self.rel_table, self.rel_model)
        if row:
            text = (f"Actividad {self.original['code']} · {self.original['name']}.\n"
                    f"Se eliminará la relación {row['pred_code']} → {row['succ_code']} "
                    f"({row['type']}, desfase {row['lag_days']} días).\n"
                    "Cambiará la precedencia entre actividades y el recálculo CPM puede modificar sus fechas.")
            self._delete_related("Relaciones", "Eliminar relación", text, "relation", row["id"],
                                 lambda: self.page.state.client.delete_relation(row["id"]), recalc=True)

    def _delete_related(self, button_key, title, text, kind, row_id, work, *, recalc):
        remove = self.remove_buttons[button_key]
        if not remove.isEnabled():
            return
        remove.setEnabled(False)
        queued = False
        def finished():
            remove.setEnabled(True)
            self.reload_related()
        try:
            if confirm(self, title, text, danger=True):
                queued = self.page.mutate(work, recalc=recalc, pid=self.pid, finished=finished,
                                          operation_key=("delete", kind, row_id))
        finally:
            if not queued:
                remove.setEnabled(True)

    def assignment_form(self, old=None):
        resources = [(None, "Demanda genérica")] + [(r["id"], r["name"]) for r in self.page.state.resources]
        fields = [Field("resource_id", "Recurso", "choice", resources), Field("skill", "Habilidad"),
                  Field("units", "Unidades", "float"), Field("work_hours", "HH presupuestadas", "float"),
                  Field("actual_hours", "HH reales", "float")]
        dlg = FormDialog("Editar asignación" if old else "Nueva asignación", fields, old or {"units": 1}, self)
        if dlg.exec():
            values = {f.key: dlg.values()[f.key] for f in fields}
            values["activity_id"] = self.original["id"]
            client, pid = self.page.state.client, self.pid
            self.page.mutate(lambda: client.update_assignment(old["id"], values) if old else client.create_assignment(pid, values),
                             self.reload_related, recalc=False, pid=pid,
                             operation_key=("assignment", old["id"] if old else None, json.dumps(values, sort_keys=True)))

    def add_assignment(self):
        self.assignment_form()

    def edit_assignment(self):
        row = self.selected(self.asg_table, self.asg_model)
        if row:
            self.assignment_form(row)

    def delete_assignment(self):
        row = self.selected(self.asg_table, self.asg_model)
        if row:
            text = (f"Actividad {self.original['code']} · {self.original['name']}.\n"
                    f"Se eliminará la asignación de {row['resource_name']} "
                    f"({row.get('skill') or 'sin habilidad especificada'}).\n"
                    "Se retirarán sus unidades y HH asignadas de la demanda de recursos de esta actividad.")
            self._delete_related("Asignaciones", "Eliminar asignación", text, "assignment", row["id"],
                                 lambda: self.page.state.client.delete_assignment(row["id"]), recalc=False)


class OperationGate(QObject):
    changed = Signal()

    def __init__(self, state):
        super().__init__(state)
        self.state = state
        self.pending = {}
        changed = getattr(state, "operations_changed", None)
        if changed is not None:
            changed.connect(self.changed.emit)

    def is_busy(self, key):
        check = getattr(self.state, "is_busy", None)
        return key in self.pending or bool(check(key) if callable(check) else False)

    def try_begin(self, key):
        if self.is_busy(key):
            return False
        begin = getattr(self.state, "try_begin", None)
        end = getattr(self.state, "end", None)
        native = callable(begin) and callable(end)
        if native and not begin(key):
            return False
        self.pending[key] = end if native else None
        self.changed.emit()
        return True

    def end(self, key):
        if key not in self.pending:
            return
        end = self.pending.pop(key)
        try:
            if end:
                end(key)
        finally:
            self.changed.emit()


def operation_gate(state):
    gate = getattr(state, "_f3_operation_gate", None)
    if gate is None:
        gate = OperationGate(state)
        state._f3_operation_gate = gate
    return gate


def status_text(code):
    translate = getattr(common, "status_text", None)
    if callable(translate):
        return translate(code)
    return {"optimo": "Óptimo", "optimal": "Óptimo", "factible": "Factible",
            "feasible": "Factible", "infactible": "No factible", "infeasible": "No factible",
            "heuristico": "Heurístico", "heuristic": "Heurístico", "unknown": "Sin determinar",
            "no_iniciada": "No iniciada", "en_curso": "En curso", "terminada": "Terminada"}.get(code, str(code or ""))


def method_text(code):
    translate = getattr(common, "method_text", None)
    if callable(translate):
        return translate(code)
    return {"auto": "Automático", "heuristic": "Heurístico", "heuristico": "Heurístico",
            "cpsat": "CP-SAT", "cp_sat": "CP-SAT", "cp-sat": "CP-SAT", "cpm": "CPM",
            "precheck": "Validación previa", "maintenance_numpy": "Nivelación de mantenimiento",
            "maintenance": "Nivelación de mantenimiento"}.get(code, str(code or ""))


def solve_report(result):
    report = {}
    for key in ("verification", "feasibility", "feasibility_report", "verification_report", "validation"):
        value = result.get(key)
        if isinstance(value, (dict, list)):
            report = value if isinstance(value, dict) else {"violations": value}
            break
    violations = result.get("violations") or report.get("violations") or []
    feasible = result.get("feasible", report.get("feasible", report.get("is_feasible")))
    if report.get("feasible") is False or report.get("is_feasible") is False or violations:
        feasible = False
    return feasible, violations


def violation_text(value):
    if not isinstance(value, dict):
        return str(value)
    text = str(value.get("detail") or value.get("message") or value)
    if value.get("activities"):
        text += " · Actividades: " + ", ".join(map(str, value["activities"]))
    if value.get("date"):
        text += " · " + common.fmt_date(value["date"])
    return text


def show_infeasible(parent, result):
    feasible, violations = solve_report(result)
    if feasible is not False:
        return
    kind = result.get("infeasibility")
    if kind == "demostrada":
        lines = ["No existe un programa factible con estos datos (infactibilidad demostrada). No se aplicó ningún cambio. "
                 "Corrige la causa indicada:"]
    elif kind == "no_encontrada":
        lines = ["No se encontró un programa factible dentro del límite de tiempo; eso no demuestra que no exista. "
                 "No se aplicó ningún cambio. Prueba con más tiempo o revisa las violaciones:"]
    else:
        lines = ["No se aplicó el programa. Revisa las violaciones:"]
    lines.extend("• " + violation_text(v) for v in violations[:20])
    if not violations:
        lines.append("El servidor no detalló las violaciones.")
    if len(violations) > 20:
        lines.append(f"… y {len(violations) - 20} más; consulta el resumen completo.")
    title = "Sin solución encontrada" if kind == "no_encontrada" else "Programa no factible"
    QMessageBox.warning(parent, title, "\n".join(lines))
