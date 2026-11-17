"""Ejemplos ficticios reproducibles. Ejecutar con python -B generar_ejemplos.py.

Sólo escribe los dos XLSX junto al script; no usa BD ni servicios externos.
"""
from __future__ import annotations

import sys
from copy import copy
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from ganttier.domain import ImportBundle
from ganttier.excel_io.schema import SCHEMAS, ENTITIES
from ganttier.excel_io.templates import build_project_template, build_maintenance_template
from ganttier.excel_io.importer import parse_workbook


ASSUMPTIONS = (
    "Datos ficticios para demostración; duraciones, dotaciones, frecuencias y costos "
    "son supuestos de planificación, no tarifas ni prescripciones normativas. "
    "CLP nominales; costo de actividades = mano de obra, sin suministro de equipos, "
    "IVA ni gastos generales. HH de avance por registro; porcentaje acumulado."
)


def calendar(name="Chile 5x2", shift=None, anchor=None):
    return dict(name=name, workweek="1111100", hours_per_day=8,
                holidays_country="CL", shift_pattern=shift, shift_anchor=anchor,
                holidays_apply_to_shift=False)


def resource(name, skills, rate, kind="trabajador", units=1, cal="Chile 5x2"):
    return dict(name=name, kind=kind, skills=skills, capacity_hours_day=8,
                max_units=units, calendar_name=cal, cost_hour=rate,
                overtime_cost_hour=rate * 1.5, max_overtime_hours_day=2, active=True)


# Paquete, disciplina, recurso, dotación, mes orientativo y seis actividades.
# La duración de fabricación incluye plazo proveedor; sus HH son de seguimiento.
PACKAGES = [
    ("IB", "Ingeniería básica", "ING", 0, 2, "2027-01", [
        ("Reunión de arranque y alcance", 1), ("Levantamiento de instalaciones existentes", 5),
        ("Estudios de flujo y cortocircuito", 10), ("Unilineal y criterios de diseño", 5),
        ("Disposición general del paño", 5), ("Ingeniería básica aprobada", 0)]),
    ("ID", "Ingeniería de detalle", "ING", 1, 2, "2027-03", [
        ("Cálculo de fundaciones y estructuras", 12), ("Diseño de malla de tierra", 8),
        ("Planos de canalizaciones y cableado", 10), ("Diseño de SS/AA CA y CC", 8),
        ("Planos de montaje y listas de cables", 10), ("Ingeniería IFC liberada", 0)]),
    ("PE", "Permisos y coordinación", "PER", 2, 1, None, [
        ("Matriz de permisos y antecedentes del predio", 5), ("Revisión de pertinencia ambiental", 20),
        ("Tramitación de permisos de obras", 30), ("Acuerdos de acceso y servidumbres", 15),
        ("Plan de seguridad para interfaz con S/E existente", 5), ("Liberación del frente de obra", 0)]),
    ("TR", "Transformador de poder 150 MVA", "ADQ", 3, 1, None, [
        ("Especificación transformador 220/66 kV 150 MVA", 8), ("Evaluación técnica y orden de compra TR-2", 15),
        ("Aprobación de planos del fabricante TR-2", 20), ("Fabricación transformador TR-2", 180),
        ("FAT transformador: dieléctricas, pérdidas y OLTC", 5), ("Transporte especial y recepción TR-2", 15)]),
    ("IN", "Interruptores 220 y 66 kV", "ADQ", 3, 1, None, [
        ("Especificación interruptores de ambos lados", 5), ("Evaluación y compra interruptores", 10),
        ("Revisión de planos de interruptores", 10), ("Fabricación interruptores", 100),
        ("FAT interruptores: tiempos y resistencia", 3), ("Recepción interruptores en obra", 8)]),
    ("DE", "Desconectadores y cuchillas de tierra", "ADQ", 3, 1, None, [
        ("Especificación desconectadores motorizados", 4), ("Compra desconectadores", 10),
        ("Revisión de planos y enclavamientos", 8), ("Fabricación desconectadores", 80),
        ("FAT mecanismos y enclavamientos", 2), ("Recepción desconectadores", 8)]),
    ("TC", "Transformadores de medida TT/CC", "ADQ", 3, 1, None, [
        ("Especificación relaciones y clases TT/CC", 5), ("Compra TT y CC", 10),
        ("Revisión de cargas y planos TT/CC", 8), ("Fabricación TT/CC", 90),
        ("FAT relación, polaridad y aislación TT/CC", 3), ("Recepción TT/CC", 8)]),
    ("PA", "Pararrayos y conectores", "ADQ", 3, 1, None, [
        ("Coordinación de aislación y selección pararrayos", 5), ("Compra pararrayos y conectores", 8),
        ("Revisión de fichas y certificados", 5), ("Fabricación pararrayos y conectores", 60),
        ("Revisión ensayos de fábrica pararrayos", 2), ("Recepción pararrayos y conectores", 5)]),
    ("FU", "Fundaciones y cubeto", "CIV", 4, 6, "2027-06", [
        ("Instalación de faena y replanteo", 5), ("Excavación fundación transformador", 8),
        ("Enfierradura y moldajes fundación TR-2", 10), ("Hormigonado fundaciones y cubeto", 4),
        ("Curado y control de resistencia del hormigón", 20), ("Terminación cortafuego y sistema de contención", 8)]),
    ("MT", "Malla de puesta a tierra", "CIV", 5, 4, "2027-07", [
        ("Medición de resistividad del terreno", 2), ("Excavación de zanjas de malla", 6),
        ("Tendido de cobre y electrodos", 8), ("Soldaduras exotérmicas e inspección", 6),
        ("Relleno y compactación de malla", 4), ("Medición continuidad y resistencia de malla", 2)]),
    ("CA", "Canaletas y drenajes", "CIV", 4, 4, "2027-07", [
        ("Replanteo de canaletas y drenajes", 3), ("Excavación canalizaciones", 6),
        ("Construcción cámaras de cables", 8), ("Montaje canaletas prefabricadas", 8),
        ("Instalación drenajes y sellos", 5), ("Inspección y entrega canalizaciones", 2)]),
    ("EM", "Montaje de equipos primarios", "MON", 6, 6, "2028-01", [
        ("Montaje estructuras y soportes", 8), ("Descarga y posicionamiento TR-2", 2),
        ("Armado radiadores, bushings y conservador", 8), ("Vacío, filtrado y llenado de aceite", 5),
        ("Montaje interruptores y desconectadores", 8), ("Montaje TT/CC y pararrayos", 6)]),
    ("CO", "Conexiones de potencia", "MON", 7, 4, "2028-02", [
        ("Montaje barras y conductores flexibles", 8), ("Instalación conectores de equipos", 5),
        ("Conexión de equipos a malla de tierra", 4), ("Ajuste mecánico de desconectadores", 4),
        ("Control de torque y distancias eléctricas", 3), ("Liberación mecánica del paño", 0)]),
    ("SA", "Servicios auxiliares CA/CC", "SAA", 8, 3, "2027-09", [
        ("Compra y fabricación tableros SS/AA", 60), ("Montaje tableros CA y CC", 5),
        ("Instalación baterías y cargadores", 4), ("Cableado fuerza, alumbrado y enchufes", 8),
        ("Pruebas autonomía y transferencia SS/AA", 3), ("Servicios auxiliares disponibles", 0)]),
    ("PR", "Protecciones del transformador", "PC", 9, 2, None, [
        ("Estudio coordinación y ajustes de protecciones", 15), ("Configuración diferencial y respaldo", 8),
        ("Fabricación y FAT tableros de protección", 40), ("Montaje tableros de protección", 4),
        ("Tendido cables de medida y disparo", 10), ("Conexionado, marcado y continuidad", 8)]),
    ("SC", "Control y SCADA", "PC", 10, 2, None, [
        ("Lista de señales y arquitectura de control", 8), ("Configuración RTU, IED y red de estación", 12),
        ("FAT control e integración SCADA", 5), ("Montaje comunicaciones y fibra óptica", 6),
        ("Integración base de datos centro de control", 8), ("Pruebas punto a punto de señales", 5)]),
    ("SP", "SAT equipos primarios", "PRU", 11, 3, "2028-03", [
        ("Protocolo SAT y revisión dossier FAT", 3), ("SAT TR-2: relación, devanados y aislación", 4),
        ("SAT interruptores: tiempos y resistencia", 3), ("SAT TT/CC: relación y polaridad", 3),
        ("Pruebas enclavamientos y mandos locales", 3), ("Cierre observaciones equipos primarios", 5)]),
    ("SI", "SAT integrado y telecontrol", "PRU", 12, 3, "2028-04", [
        ("Inyección secundaria de relés", 5), ("Pruebas disparo extremo a extremo", 4),
        ("Pruebas falla interruptor y bloqueos", 3), ("SAT SCADA con centro de control", 4),
        ("Simulacro secuencia de energización", 2), ("Cierre observaciones de control y protecciones", 4)]),
    ("EN", "Energización", "PES", 13, 2, "2028-05", [
        ("Dossier técnico para autorización de conexión", 10), ("Coordinación de ventana de desconexión", 15),
        ("Revisión y aprobación programa de maniobras", 5), ("Conexión final a barras existentes", 2),
        ("Energización en vacío y toma de carga", 1), ("Monitoreo inicial bajo carga", 5)]),
    ("PS", "Puesta en servicio y cierre", "PES", 14, 2, "2028-06", [
        ("Termografía inicial bajo carga", 1), ("Verificación medición comercial y registros", 2),
        ("Capacitación operación y mantenimiento", 3), ("Entrega planos conforme a obra", 8),
        ("Recepción operacional y cierre pendientes", 5), ("Puesta en servicio comercial", 0)]),
]


def project_bundle():
    b = ImportBundle(mode="proyecto", project=dict(
        name="Ampliación S/E 220/66 kV – paño de transformador 150 MVA",
        mode="proyecto", start_date=date(2027, 1, 4), end_date=date(2028, 6, 30),
        data_date=date(2027, 1, 22), calendar_name="Chile 5x2", currency="CLP",
        description=ASSUMPTIONS + " S/E ficticia Alto Maitén. Horizonte enero 2027–junio 2028."))
    b.calendars = [calendar()]
    specs = [
        ("Ingeniería de sistemas", ["ingenieria"], 32000, 2),
        ("Ingeniería de detalle", ["ingenieria", "civil"], 28000, 2),
        ("Especialista permisos", ["permisos"], 26000, 1),
        ("Gestión de suministros", ["adquisiciones"], 24000, 1),
        ("Cuadrilla civil", ["civil"], 12000, 6),
        ("Cuadrilla puesta a tierra", ["tierra", "electrico"], 15000, 4),
        ("Cuadrilla montaje primario", ["montaje"], 17000, 6),
        ("Cuadrilla conexiones", ["montaje", "electrico"], 17000, 4),
        ("Cuadrilla SS/AA", ["ssaa", "electrico"], 18000, 3),
        ("Ingeniería protecciones", ["protecciones"], 31000, 2),
        ("Ingeniería SCADA", ["scada"], 30000, 2),
        ("Especialistas SAT primario", ["pruebas", "montaje"], 29000, 3),
        ("Especialistas SAT integrado", ["protecciones", "scada"], 31000, 3),
        ("Equipo operación y maniobras", ["operacion"], 25000, 2),
        ("Equipo puesta en servicio", ["comisionamiento", "termografia"], 28000, 2),
        ("Inspección técnica de obra", ["calidad", "civil"], 27000, 1),
        ("Prevención de riesgos", ["seguridad"], 22000, 1),
        ("Operador de grúa", ["izaje"], 20000, 1),
    ]
    b.resources = [resource(n, s, r, "cuadrilla" if u > 1 else "trabajador", u)
                   for n, s, r, u in specs]
    b.wbs = [dict(code="SE", name="S/E Alto Maitén — ampliación TR-2", sort_order=0)]
    disciplines = dict(ING="Ingeniería", PER="Permisos", ADQ="Adquisiciones de equipos primarios",
                       CIV="Obras civiles", MON="Montaje electromecánico", SAA="SS/AA",
                       PC="Protecciones y control/SCADA", PRU="Pruebas FAT/SAT", PES="Energización y puesta en servicio")
    for order, (code, name) in enumerate(disciplines.items(), 1):
        b.wbs.append(dict(code=f"SE.{code}", name=name, parent_code="SE", sort_order=order))
    for order, (tag, title, disc, ri, crew, month, tasks) in enumerate(PACKAGES, 1):
        wbs = f"SE.{disc}.{tag}"
        b.wbs.append(dict(code=wbs, name=title, parent_code=f"SE.{disc}", sort_order=order))
        r = b.resources[ri]
        for j, (name, duration) in enumerate(tasks, 1):
            code = f"{tag}{j:02d}"
            # Espera/fabricación: 0,5 HH por día y persona para seguimiento.
            followup = "Fabricación" in name or "fabricación" in name or "Curado" in name
            hh = duration * crew * (0.5 if followup else 8)
            a = dict(code=code, name=name, wbs_code=wbs, duration_days=duration,
                     work_hours=hh, cost_budget=hh * r["cost_hour"],
                     macro_month=month if j in (1, 2) else None, window_hard=False,
                     constraint_type="ASAP", priority=90 if tag in ("TR", "EN") else 50,
                     calendar_name="Chile 5x2", required_skill=r["skills"][0], crew_size=crew if duration else 0,
                     location="S/E Alto Maitén", equipment="TR-2 / paño 220/66 kV",
                     voltage_kv=220, requires_outage=tag == "EN" and j in (4, 5),
                     discipline=disciplines[disc], notes="Hito" if not duration else
                     ("HH de seguimiento; plazo de proveedor/curado." if followup else "Estimación de HH directas de la dotación."))
            b.activities.append(a)
            if duration:
                b.assignments.append(dict(activity_code=code, resource_name=r["name"],
                                          skill=r["skills"][0], units=crew, work_hours=hh, actual_hours=0))
            if j > 1:
                b.relations.append(dict(pred_code=f"{tag}{j-1:02d}", succ_code=code, type="FS", lag_days=0))
    def link(pred, succ, kind="FS", lag=0):
        b.relations.append(dict(pred_code=pred, succ_code=succ, type=kind, lag_days=lag))
    for tag in ("ID", "TR", "IN", "DE", "TC", "PA", "PR", "SC"):
        link("IB06", tag + "01")
    link("IB01", "PE01", "SS", 2)
    for tag in ("FU", "MT", "CA", "SA"):
        link("ID06", tag + "01")
    link("PE06", "FU01")
    link("FU01", "MT02", "SS", 5)
    link("MT04", "CA06", "FF", 2)
    for pred in ("FU06", "TR06", "IN06", "DE06", "TC06", "PA06", "MT06"):
        link(pred, "EM01")
    link("EM06", "CO01")
    link("CA06", "PR05")
    link("SA06", "SC06")
    for pred in ("CO06", "SA06", "PR06", "SC06"):
        link(pred, "SP01")
    link("SP06", "SI01")
    link("SI06", "EN01")
    link("EN06", "PS01")
    for code, kind, when in [("FU01", "SNET", date(2027, 6, 1)),
                             ("EN04", "SNET", date(2028, 5, 2)),
                             ("PS06", "FNLT", date(2028, 6, 30))]:
        a = next(a for a in b.activities if a["code"] == code)
        a.update(constraint_type=kind, constraint_date=when)
    # Apoyos adicionales; incorporados también al presupuesto de cada actividad.
    for code, ri, hh in [("FU04", 15, 16), ("EM02", 16, 16), ("EM02", 17, 16), ("EN04", 16, 16)]:
        r = b.resources[ri]
        b.assignments.append(dict(activity_code=code, resource_name=r["name"], skill=r["skills"][0],
                                  units=1, work_hours=hh, actual_hours=0))
        a = next(a for a in b.activities if a["code"] == code)
        a["work_hours"] += hh
        a["cost_budget"] += hh * r["cost_hour"]
        a["crew_size"] += 1
    # Corte de las tres primeras semanas; HH incrementales y porcentajes acumulados.
    entries = [
        ("IB01", 8, 100, 16, 4, 4), ("IB02", 8, 80, 64, 5, None),
        ("PE01", 8, 60, 24, 6, None), ("IB02", 15, 100, 16, 5, 11),
        ("IB03", 15, 40, 64, 12, None), ("PE01", 15, 100, 16, 6, 12),
        ("PE02", 15, 15, 24, 13, None), ("IB03", 22, 90, 80, 12, None),
        ("PE02", 22, 40, 40, 13, None),
    ]
    for code, day, pct, hh, start, finish in entries:
        assignment = next(x for x in b.assignments if x["activity_code"] == code)
        r = next(r for r in b.resources if r["name"] == assignment["resource_name"])
        assignment["actual_hours"] += hh
        b.progress.append(dict(activity_code=code, date=date(2027, 1, day), percent_complete=pct,
                               actual_hours=hh, actual_cost=hh * r["cost_hour"],
                               resource_name=r["name"], actual_start=date(2027, 1, start),
                               actual_finish=date(2027, 1, finish) if finish else None,
                               note="HH del período; avance físico acumulado al corte semanal."))
    return b


def maintenance_bundle():
    b = ImportBundle(mode="mantenimiento", project=dict(
        name="Plan anual 2027 — Red ficticia Valle Austral", mode="mantenimiento",
        start_date=date(2027, 1, 1), end_date=date(2027, 12, 31), data_date=date(2027, 1, 1),
        calendar_name="Chile 5x2", currency="CLP", description=ASSUMPTIONS))
    b.calendars = [calendar(), calendar("Brigada 7x7 A", "7x7", date(2027, 1, 1)),
                   calendar("Brigada 7x7 B", "7x7", date(2027, 1, 8))]
    names = ["Alba Ríos", "Bruno Vega", "Camila Prado", "Diego Soto", "Elena Lagos", "Felipe Mora",
             "Gabriela Solís", "Hugo Parra", "Inés Vidal", "Javier Leiva", "Karen Pino", "Luis Araya",
             "María Rojas", "Nicolás Vera", "Olivia Silva", "Pablo Reyes", "Quena Fuentes", "Rafael Luna",
             "Sara Muñoz", "Tomás Castro", "Úrsula Salas", "Víctor Olmos", "Wanda Peña", "Ximena Torres",
             "Yago Molina", "Zoe Valdés", "Andrés Robles", "Beatriz Arce", "Cristóbal Cano", "Daniela Paz"]
    skills = ["inspeccion", "termografia", "aceite", "protecciones", "interruptores", "tierra"]
    rates = [16000, 22000, 21000, 27000, 20000, 19000]
    for i, name in enumerate(names):
        cal = "Chile 5x2" if i < 18 else ("Brigada 7x7 A" if i < 24 else "Brigada 7x7 B")
        b.resources.append(resource(f"T{i+1:02d} · {name} (ficticio)",
                                    [skills[i % 6], "electrico", "lineas", "lavado"], rates[i % 6], cal=cal))
    stations = ["Alto Maitén", "Las Brumas", "Valle Claro", "Piedra Azul", "Los Arrayanes", "Loma Serena",
                "Río Seco", "Monte Canelo", "Quebrada Verde", "Laguna Norte", "Portal del Sur", "Cerro Lucero"]
    # nombre, frecuencia, meses, duración, dotación, habilidad, desconexión, tolerancias
    base = [
        ("Inspección visual de patio y equipos", "M", list(range(1, 13)), 1, 2, "inspeccion", False, 3, 5),
        ("Termografía de conexiones bajo carga", "Q", [1, 4, 7, 10], 1, 2, "termografia", False, 7, 10),
        ("Muestreo de aceite para análisis DGA", "S", [3, 9], 1, 2, "aceite", False, 10, 15),
        ("Pruebas anuales de relés y circuitos de disparo", "A", [5], 2, 3, "protecciones", True, 14, 14),
    ]
    def add(location, equipment, voltage, spec, offset, fixed=False):
        name, freq, months, days, crew, skill, outage, before, after = spec
        eligible = [r for r in b.resources if skill in r["skills"]]
        worker = eligible[offset % len(eligible)]
        b.periodic_tasks.append(dict(
            code=f"PM{len(b.periodic_tasks)+1:03d}", name=f"{location} — {name}", frequency=freq,
            interval=6 if freq == "NM" else 1, duration_days=days, work_hours=days * crew * 8,
            crew_size=crew, required_skill=skill, resource_name=worker["name"] if fixed else None,
            first_due=date(2027, months[0], 10 + offset % 12), preferred_months=",".join(map(str, months)),
            preferred_weekday=2, day_of_month=10 + offset % 12, tol_before_days=before,
            tol_after_days=after, window_hard=outage, location=location, equipment=equipment,
            voltage_kv=voltage, requires_outage=outage, discipline="Líneas" if location.startswith("LT") else "Subestaciones",
            calendar_name=worker["calendar_name"] if fixed else "Chile 5x2", priority=80 if outage else 50,
            cost_per_occurrence=days * crew * 8 * worker["cost_hour"], active=True, horizon_end=date(2027, 12, 31)))
    for i, name in enumerate(stations):
        fifth = (("Mantenimiento interruptores: mecanismo, tiempos y resistencia", "NM", [4, 10], 2, 3, "interruptores", True, 14, 14)
                 if i % 2 == 0 else
                 ("Medición de malla de tierra y continuidad", "A", [2], 1, 3, "tierra", True, 14, 14))
        for j, spec in enumerate(base + [fifth]):
            equipment = ["Patio completo", "Conexiones primarias", "TR-1", "Protecciones paño TR-1",
                         "Interruptores paño TR-1" if i % 2 == 0 else "Malla de tierra"][j]
            add("S/E " + name, equipment, 220 if i < 6 else 66, spec, i, fixed=j == 0)
    for i in range(6):
        loc = f"LT {stations[2*i]}–{stations[2*i+1]}"
        add(loc, f"Cadena de aislación circuito 1 — tramo {20+i*5} km", 220,
            ("Lavado de aislación con línea desenergizada", "S", [3, 9], 2, 4, "lavado", True, 14, 14), i)
        add(loc, f"Franja de servidumbre y estructuras — tramo {20+i*5} km", 220,
            ("Inspección visual de franja de servidumbre", "M", list(range(1, 13)), 2, 2, "lineas", False, 5, 7),
            18 + i, fixed=True)
    return b


def write_template(bundle, path):
    builder = build_project_template if bundle.mode == "proyecto" else build_maintenance_template
    builder(path)
    wb = load_workbook(path)
    try:
        original_sheets = list(wb.sheetnames)
        for i, col in enumerate(SCHEMAS["Proyecto"], 2):
            wb["Proyecto"].cell(i, 2, bundle.project.get(col.field))
        for title in original_sheets:
            if title not in SCHEMAS or title == "Proyecto":
                continue
            ws = wb[title]
            cols = SCHEMAS[title]
            assert [c.value for c in ws[1]] == [c.title for c in cols]
            styles = [copy(c._style) for c in ws[2]]
            ws.delete_rows(2, ws.max_row)
            records = [] if title == "Excepciones_Calendario" else getattr(bundle, ENTITIES[title][0])
            for rownum, record in enumerate(records, 2):
                for idx, col in enumerate(cols, 1):
                    value = record.get(col.field)
                    if isinstance(value, list):
                        value = ",".join(map(str, value))
                    if isinstance(value, bool):
                        value = "SI" if value else "NO"
                    cell = ws.cell(rownum, idx, value)
                    cell._style = copy(styles[idx-1])
                ws.row_dimensions[rownum].height = 42
            for table in ws.tables.values():
                table.ref = f"A1:{get_column_letter(len(cols))}{max(2, len(records)+1)}"
                if table.autoFilter:
                    table.autoFilter.ref = table.ref
        wb["Instrucciones"].append(["Supuestos del ejemplo", ASSUMPTIONS])
        last = wb["Instrucciones"].max_row
        for i in (1, 2):
            wb["Instrucciones"].cell(last, i)._style = copy(wb["Instrucciones"].cell(2, i)._style)
        wb["Instrucciones"].row_dimensions[last].height = 65
        assert wb.sheetnames == original_sheets
        wb.save(path)
    finally:
        wb.close()


def validate(path, expected):
    parsed = parse_workbook(path, expected.mode)
    if parsed.issues:
        raise ValueError(f"{path.name}: {parsed.issues}")
    for field in ("calendars", "resources", "wbs", "activities", "relations", "assignments", "periodic_tasks", "progress"):
        assert len(getattr(parsed, field)) == len(getattr(expected, field)), field
    workers = {r["name"]: r for r in parsed.resources}
    for a in parsed.activities:
        assignments = [x for x in parsed.assignments if x["activity_code"] == a["code"]]
        assert sum(x["work_hours"] for x in assignments) == a["work_hours"], a["code"]
        for x in assignments:
            assert x["skill"] in workers[x["resource_name"]]["skills"]
            assert x["units"] <= workers[x["resource_name"]]["max_units"]
    for task in parsed.periodic_tasks:
        assert any(task["required_skill"] in r["skills"] for r in parsed.resources)
        if task.get("resource_name"):
            assert task["required_skill"] in workers[task["resource_name"]]["skills"]
    if parsed.mode == "proyecto":
        assert len(parsed.activities) == 120 and 15 <= len(parsed.resources) <= 20
        assert {r["type"] for r in parsed.relations} >= {"FS", "SS", "FF"}
        parents = {w["code"]: w.get("parent_code") for w in parsed.wbs}
        for a in parsed.activities:
            code, depth = a["wbs_code"], 0
            while code:
                depth += 1
                code = parents[code]
            assert depth == 3
    else:
        locations = {t["location"] for t in parsed.periodic_tasks}
        assert sum(x.startswith("S/E") for x in locations) == 12
        assert sum(x.startswith("LT") for x in locations) == 6
        assert 60 <= len(parsed.periodic_tasks) <= 80 and len(parsed.resources) == 30
        assert any(c.get("shift_pattern") == "7x7" for c in parsed.calendars)
    counts = {f: len(getattr(parsed, f)) for f in
              ("activities", "wbs", "relations", "resources", "assignments", "periodic_tasks", "progress", "calendars")}
    print(f"{path.name}: {counts}; hitos={sum(a['duration_days'] == 0 for a in parsed.activities)}; "
          f"validación parse_workbook: 0 errores, 0 advertencias.")


def main():
    for bundle, filename in [(project_bundle(), "Ejemplo_Proyecto_SE_220kV.xlsx"),
                             (maintenance_bundle(), "Ejemplo_Plan_Mantenimiento.xlsx")]:
        path = HERE / filename
        write_template(bundle, path)
        validate(path, bundle)


if __name__ == "__main__":
    main()
