"""Lectura secuencial de Excel y validación sin acceso a la base de datos."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Any, get_args, get_type_hints
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl.utils.datetime import from_excel

from ganttier.domain import ImportBundle, ImportIssue
from .schema import CHOICES, ENTITIES, SCHEMAS, Column, all_columns, normalize
from .interchange import MARKER, decode
from .reader import rows as worksheet_rows, load_read_only

_CHOICE_LOOKUPS = {kind: {normalize(value): value for value in values} for kind, values in CHOICES.items()}
_CACHE_KINDS = {"bool", "date", "month", "months", "week", "shift", *CHOICES}


def _convert(value: Any, column: Column, epoch: datetime) -> Any:
    kind = column.kind
    if kind == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            try:
                converted = from_excel(value, epoch)
            except (ValueError, OverflowError):
                raise ValueError('Fecha Excel fuera del rango admitido.') from None
            if isinstance(converted, datetime):
                return converted.date()
            raise ValueError("Fecha Excel inválida.")
        for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(str(value).strip(), pattern).date()
            except ValueError:
                pass
        raise ValueError("Fecha inválida; use AAAA-MM-DD o DD-MM-AAAA.")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().casefold().replace('í', 'i')
        if text in {"si", "1", "true"}:
            return True
        if text in {"no", "0", "false"}:
            return False
        raise ValueError("Use SI/NO o 1/0.")
    if kind in {"number", "int", "signed_int", "positive_int", "priority", "percent", "weekday", "monthday"}:
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                number = value
            else:
                text = str(value).strip().replace(" ", "")
                text = text.replace('\u00a0', '')
                if kind == 'percent' and text.endswith('%'):
                    text = text[:-1]
                if ',' in text and '.' in text:
                    decimal, thousands = (',', '.') if text.rfind(',') > text.rfind('.') else ('.', ',')
                    whole, fraction = text.rsplit(decimal, 1)
                    groups = whole.lstrip('+-').split(thousands)
                    if not (1 <= len(groups[0]) <= 3 and all(len(g) == 3 and g.isdigit() for g in groups[1:]) and fraction.isdigit()):
                        raise ValueError('Separadores numéricos inválidos.')
                    text = whole.replace(thousands, '') + '.' + fraction
                elif ',' in text:
                    text = text.replace(',', '.')
                number = float(text)
        except (ValueError, OverflowError):
            raise ValueError("Se requiere un número válido.") from None
        if not isfinite(number):
            raise ValueError("Se requiere un número finito.")
        integer = kind not in {"number", "percent"}
        if integer and number != int(number):
            raise ValueError("Se requiere un número entero.")
        low = 1 if kind in {"positive_int", "monthday"} else 0
        high = {"priority": 100, "percent": 100, "weekday": 6, "monthday": 31}.get(kind)
        if kind != "signed_int" and (number < low or (high is not None and number > high)):
            raise ValueError(f"Valor fuera de rango: mínimo {low}" + (f", máximo {high}." if high is not None else "."))
        return int(number) if integer else number
    text = str(value).strip()
    if kind in CHOICES:
        lookup = _CHOICE_LOOKUPS[kind]
        key = normalize(text)
        if key not in lookup:
            raise ValueError("Valor inválido; use " + ", ".join(CHOICES[kind]) + ".")
        return lookup[key]
    if kind == "skills":
        return [part.strip() for part in text.split(",") if part.strip()]
    if kind == "week" and not re.fullmatch("[01]{7}", text):
        raise ValueError("Semana laboral: siete dígitos 0/1 de lunes a domingo.")
    if kind == "shift" and not re.fullmatch(r"[1-9]\d*x(?:0|[1-9]\d*)", text.lower()):
        raise ValueError("Turno inválido; use, por ejemplo, 7x7.")
    if kind == "month":
        if not re.fullmatch(r"\d{4}-\d{2}", text):
            raise ValueError("Mes macro inválido; use AAAA-MM.")
        try:
            date.fromisoformat(text + "-01")
        except ValueError:
            raise ValueError("Mes macro inexistente.") from None
    if kind == "months":
        try:
            months = [int(x.strip()) for x in text.split(",")]
        except ValueError:
            raise ValueError("Use meses del 1 al 12 separados por coma.") from None
        if any(x < 1 or x > 12 for x in months):
            raise ValueError("Use meses del 1 al 12 separados por coma.")
        return ",".join(map(str, months))
    return text.lower() if kind == "shift" else text


@lru_cache(maxsize=None)
def _field_types(sheet: str) -> dict:
    return get_type_hints(ENTITIES[sheet][1]) if sheet in ENTITIES else {}


LARGE_PROJECT_ACTIVITIES = 100_000  # tamaño con el que se verificó el rendimiento (certificación del 06-10-2026)


def _external_links(path: str | Path) -> int:
    """Cantidad de libros externos enlazados. Ganttier sólo lee los valores guardados y no actualiza vínculos."""
    try:
        with ZipFile(path) as archive:
            return sum(1 for name in archive.namelist() if name.startswith('xl/externalLinks/externalLink') and name.endswith('.xml'))
    except (BadZipFile, OSError):
        return 0


def parse_workbook(path: str | Path, mode: str | None = None) -> ImportBundle:
    """Importa filas con referencias legibles y errores por hoja/fila/columna.

    Las fechas se entregan como date; porcentajes físicos en escala 0..100.
    Un archivo ilegible también se expresa mediante ImportIssue.
    """
    bundle = ImportBundle(mode="proyecto", project={})
    positions: dict[str, list[int]] = {}
    field_titles: dict[str, dict[str, str]] = {}
    cached_convert = lru_cache(maxsize=2048)(_convert)

    def issue(sheet: str, row: int, field: str, message: str, severity: str = "error") -> None:
        bundle.issues.append(ImportIssue(sheet, row, field_titles.get(sheet, {}).get(field, field), message, severity))

    try:
        workbook = load_read_only(path)
    except Exception as exc:
        # openpyxl utiliza varias excepciones ZIP/XML según el daño del archivo.
        issue("Libro", 0, "Archivo", f"No se pudo leer el archivo Excel: {exc}")
        return bundle
    links = _external_links(path)
    if links:
        issue("Libro", 0, "Vínculos externos",
              f"El libro contiene {links} vínculo(s) a otros archivos de Excel. Ganttier lee sólo los valores guardados y no "
              "actualiza vínculos: verifique que esos valores correspondan a los datos que desea importar.", "advertencia")
    try:
        exported = '_Ganttier' in workbook.sheetnames and workbook['_Ganttier']['A1'].value == MARKER
        export_mode = workbook['_Ganttier']['B1'].value if exported else None
        project_mode_present = False

        def read_value(cell, col):
            if cell is None:
                if col.required:
                    raise ValueError('Campo obligatorio vacío.')
                return False, None
            if cell.data_type == 'f':
                raise ValueError('No se admiten fórmulas; pegue su resultado como valor.')
            if cell.data_type == 'e':
                raise ValueError(f'La celda contiene un error de Excel: {cell.value}.')
            value = cell.value
            if exported:
                present, value = decode(value)
                if not present:
                    if col.required and col.field != 'mode':
                        raise ValueError('Campo obligatorio ausente.')
                    return False, None
                if value is None:
                    if col.required:
                        raise ValueError('Campo obligatorio vacío.')
                    return True, None
                if col.field == 'exceptions':
                    if value == []:
                        return True, []
                    raise ValueError('Edite las excepciones en la hoja Excepciones_Calendario.')
                if isinstance(value, list) and col.kind != 'skills':
                    raise ValueError('Este campo no admite una lista de valores.')
                if col.kind == 'text':
                    return True, str(value)
                if col.kind == 'skills' and isinstance(value, list):
                    return True, value
            if value is None or (isinstance(value, str) and not value.strip()):
                if col.required:
                    raise ValueError('Campo obligatorio vacío.')
                return False, None
            if col.kind == 'percent' and isinstance(value, (int, float)) and '%' in re.sub(r'"[^"]*"|\\.', '', cell.number_format):
                value *= 100
            convert = cached_convert if col.kind in _CACHE_KINDS else _convert
            return True, convert(value, col, workbook.epoch)

        sheet_names = {}
        for name in workbook.sheetnames:
            key = normalize(name)
            if key in sheet_names:
                issue(name, 1, "Hoja", "Hay dos hojas con el mismo nombre normalizado.")
            sheet_names[key] = name
        project_sheet = sheet_names.get("proyecto")
        project_rows: dict[str, int] = {}
        if not project_sheet:
            issue("Proyecto", 1, "Hoja", "Falta la hoja Proyecto.")
        else:
            mapping = {normalize(key): c for c in all_columns("Proyecto") for key in (c.title, c.field)}
            workbook[project_sheet].reset_dimensions()
            for rownum, row in worksheet_rows(workbook[project_sheet]):
                if len(row) < 2 or row[0].value is None:
                    continue
                col = mapping.get(normalize(row[0].value))
                if col is None:
                    continue
                field_titles.setdefault("Proyecto", {})[col.field] = str(row[0].value)
                if col.field in project_rows:
                    issue("Proyecto", rownum, col.field, "Clave duplicada.")
                project_rows[col.field] = rownum
                try:
                    present, value = read_value(row[1], col)
                    if present:
                        bundle.project[col.field] = value
                        project_mode_present |= col.field == 'mode'
                except (ValueError, OverflowError, TypeError) as exc:
                    issue("Proyecto", rownum, col.field, str(exc))
        detected = bundle.project.get("mode") or export_mode
        if mode is not None and mode not in CHOICES["mode"]:
            issue("Proyecto", project_rows.get("mode", 1), "Modo", "Modo inválido; use proyecto o mantenimiento.")
        elif mode is not None:
            if detected and mode != detected:
                issue("Proyecto", project_rows.get("mode", 1), "Modo", "El modo solicitado no coincide con el del libro.")
            bundle.mode = mode
        elif detected in CHOICES['mode']:
            bundle.mode = detected
        bundle.project["mode"] = bundle.mode
        for col in SCHEMAS["Proyecto"]:
            if col.required and col.field != "mode" and not bundle.project.get(col.field):
                issue("Proyecto", project_rows.get(col.field, 1), col.title, "Campo obligatorio ausente o inválido.")
        if detected is None and mode is None:
            issue("Proyecto", project_rows.get("mode", 1), "Modo", "Debe indicar el modo en la hoja Proyecto.")
        if bundle.mode == "mantenimiento" and not bundle.project.get("end_date"):
            issue("Proyecto", project_rows.get("end_date", 1), "Fecha_Fin", "El horizonte final es obligatorio en mantenimiento.")

        exceptions = []
        route_records = []
        for sheet in SCHEMAS:
            if sheet == "Proyecto" or normalize(sheet) not in sheet_names:
                continue
            worksheet = workbook[sheet_names[normalize(sheet)]]
            # Algunos productores declaran A1:A1 aunque exista un libro completo.
            worksheet.reset_dimensions()
            iterator = worksheet_rows(worksheet)
            header_number, header_cells = next(iterator, (1, []))
            header = [cell.value for cell in header_cells] if header_number == 1 else []
            mapping = {normalize(key): c for c in all_columns(sheet) for key in (c.title, c.field)}
            indexed = []
            used = set()
            for idx, value in enumerate(header):
                col = mapping.get(normalize(value))
                if col:
                    if col.field in used:
                        issue(sheet, 1, str(value), "Columna duplicada.")
                    else:
                        indexed.append((idx, col))
                        used.add(col.field)
                        field_titles.setdefault(sheet, {})[col.field] = str(value)
                elif value:
                    issue(sheet, 1, str(value), "Columna no reconocida; se omite.", "advertencia")
            for col in SCHEMAS[sheet]:
                if col.required and col.field not in used:
                    issue(sheet, 1, col.title, "Falta la columna obligatoria.")
            destination = route_records if sheet == "Rutas" else exceptions if sheet == "Excepciones_Calendario" else getattr(bundle, ENTITIES[sheet][0])
            positions[sheet] = []
            for rownum, row in iterator:
                values = tuple(cell.value for cell in row)
                if not any(value is not None and (not isinstance(value, str) or value.strip()) for value in values):
                    continue
                item = {}
                for idx, col in indexed:
                    try:
                        present, value = read_value(row[idx] if idx < len(row) else None, col)
                        if present:
                            typ = _field_types(sheet).get(col.field)
                            if value is None and typ is not None and type(None) not in get_args(typ):
                                raise ValueError('El campo no admite NULL.')
                            item[col.field] = value
                    except (ValueError, OverflowError, TypeError) as exc:
                        issue(sheet, rownum, col.field, str(exc))
                if sheet == "Rutas":
                    item["is_route"] = True
                destination.append(item)
                positions[sheet].append(rownum)

        bundle.periodic_tasks.extend(route_records)
        positions.setdefault("Plan_Maestro", []).extend(positions.get("Rutas", []))

        def rows(sheet: str):
            return zip(positions.get(sheet, []), getattr(bundle, ENTITIES[sheet][0]))

        indexes = {}
        for sheet, key in (("Calendarios", "name"), ("Recursos", "name"), ("Equipos", "name"), ("WBS", "code"), ("Actividades", "code"), ("Plan_Maestro", "code")):
            index = {}
            for rownum, item in rows(sheet):
                value = item.get(key)
                if value in index:
                    issue(sheet, rownum, key, f"Identificador duplicado: {value}.")
                if value:
                    index[value] = item
            indexes[sheet] = index

        # Equipos también son recursos: las asignaciones pueden referenciarlos por nombre.
        indexes["Recursos"].update(indexes["Equipos"])
        for rownum, item in rows("Plan_Maestro"):
            for name in item.get("team_names", []):
                if name not in indexes["Equipos"]:
                    issue("Plan_Maestro", rownum, "team_names", f"Equipo de trabajo inexistente: {name}.")
            if any(item.get(k) is not None for k in ("quantity_total", "batch_size", "productivity_day_team", "productivity_hour_team")) or item.get("team_names"):
                from ganttier.teams import validate_batch_fields
                try:
                    validate_batch_fields(dict(item, team_ids=[i+1 for i, _ in enumerate(item.get("team_names", []))]))
                    if len(set(item.get("team_names", []))) != len(item.get("team_names", [])):
                        raise ValueError("La lista de equipos contiene duplicados.")
                except ValueError as exc:
                    issue("Plan_Maestro", rownum, "team_names", str(exc))
        for rownum, item in rows("Equipos"):
            if item.get("hours_day", 0) <= 0 or item.get("hours_day", 0) > 24:
                issue("Equipos", rownum, "hours_day", "Horas_Dia debe ser mayor que cero y no superar 24.")
            if "," in item.get("name", ""):
                issue("Equipos", rownum, "name", "El nombre de equipo no puede contener comas.")

        def reference(sheet: str, rownum: int, item: dict, field: str, target: str) -> None:
            value = item.get(field)
            if value and value not in indexes[target]:
                issue(sheet, rownum, field, f"Referencia inexistente: {value}.")

        for sheet, checks in {
            "Recursos": [("calendar_name", "Calendarios")], "WBS": [("parent_code", "WBS")],
            "Actividades": [("team_name", "Equipos"), ("calendar_name", "Calendarios"), ("wbs_code", "WBS"), ("periodic_task_code", "Plan_Maestro")],
            "Plan_Maestro": [("route_code", "Plan_Maestro"), ("calendar_name", "Calendarios"), ("resource_name", "Recursos"), ("wbs_code", "WBS")],
            "Relaciones": [("pred_code", "Actividades"), ("succ_code", "Actividades")],
            "Asignaciones": [("activity_code", "Actividades"), ("resource_name", "Recursos")],
            "Avance": [("resource_name", "Recursos")],
        }.items():
            for rownum, item in rows(sheet):
                for field, target in checks:
                    reference(sheet, rownum, item, field, target)
                if sheet == "Asignaciones" and not (item.get("resource_name") or item.get("skill")):
                    issue(sheet, rownum, "Recurso", "Indique un recurso o una habilidad.")
                if sheet == "Relaciones" and item.get("pred_code") == item.get("succ_code"):
                    issue(sheet, rownum, "succ_code", "Una actividad no puede precederse a sí misma.")
                if sheet == "Avance":
                    code = item.get("activity_code", "")
                    task, separator, index = code.rpartition("-")
                    occurrence = bundle.mode == "mantenimiento" and separator and task in indexes["Plan_Maestro"] and index.isdigit() and int(index) >= 1
                    if code not in indexes["Actividades"] and not occurrence:
                        issue(sheet, rownum, "activity_code", f"Actividad u ocurrencia inexistente: {code}.")
        reference("Proyecto", project_rows.get("calendar_name", 1), bundle.project, "calendar_name", "Calendarios")
        exception_keys = set()
        for rownum, item in zip(positions.get("Excepciones_Calendario", []), exceptions):
            reference("Excepciones_Calendario", rownum, item, "calendar_name", "Calendarios")
            key = (item.get("calendar_name"), item.get("date"))
            if key in exception_keys:
                issue("Excepciones_Calendario", rownum, "Fecha", "Excepción duplicada para el calendario y fecha.")
            exception_keys.add(key)
            calendar = indexes["Calendarios"].get(item.get("calendar_name"))
            if calendar is not None:
                if calendar.get('exceptions') is None:
                    calendar['exceptions'] = []
                calendar.setdefault("exceptions", []).append({k: v for k, v in item.items() if k != "calendar_name"})
        for item in bundle.calendars:
            if not exported:
                item.setdefault("exceptions", [])

        # DFS iterativa O(V+E): identifica una arista real del ciclo, sin culpar
        # a sucesoras aguas abajo ni agotar la pila con redes de 100.000 nodos.
        adjacency = {}
        for rownum, item in rows('Relaciones'):
            pred, succ = item.get('pred_code'), item.get('succ_code')
            if pred in indexes['Actividades'] and succ in indexes['Actividades'] and pred != succ:
                adjacency.setdefault(pred, []).append((succ, rownum))
        state = {}
        for root in adjacency:
            if state.get(root):
                continue
            state[root] = 1
            stack = [(root, iter(adjacency[root]))]
            while stack:
                node, edges = stack[-1]
                edge = next(edges, None)
                if edge is None:
                    state[node] = 2
                    stack.pop()
                    continue
                succ, rownum = edge
                if state.get(succ) == 1:
                    issue('Relaciones', rownum, 'succ_code', f'La relación {node} → {succ} cierra un ciclo de precedencias.')
                elif not state.get(succ):
                    state[succ] = 1
                    stack.append((succ, iter(adjacency.get(succ, ()))))

        # Validación O(n) de jerarquías; evita recursión en WBS profundas.
        done = set()
        wbs_rows = {item.get("code"): rownum for rownum, item in rows("WBS")}
        for code in indexes["WBS"]:
            chain = set()
            current = code
            while current and current in indexes["WBS"] and current not in done:
                if current in chain:
                    rownum = wbs_rows[current]
                    issue("WBS", rownum, "Codigo_Padre", "La jerarquía WBS contiene un ciclo.")
                    break
                chain.add(current)
                current = indexes["WBS"][current].get("parent_code")
            done.update(chain)

        for sheet in ("Actividades", "Avance", "Calendarios", "Plan_Maestro", "Proyecto"):
            records = [(1, bundle.project)] if sheet == "Proyecto" else rows(sheet)
            for rownum, item in records:
                for start, finish in (("start_date", "end_date"), ("window_start", "window_end"), ("actual_start", "actual_finish"), ("planned_start", "planned_finish"), ("first_due", "horizon_end")):
                    if item.get(start) and item.get(finish) and item[start] > item[finish]:
                        issue(sheet, project_rows.get(finish, rownum) if sheet == 'Proyecto' else rownum, finish, "La fecha final es anterior a la inicial.")
                if sheet == "Actividades" and item.get("constraint_type", "ASAP") not in {"ASAP", "ALAP"} and not item.get("constraint_date"):
                    issue(sheet, rownum, "constraint_date", "La restricción requiere una fecha.")
                if sheet == "Calendarios" and item.get("shift_pattern") and not item.get("shift_anchor"):
                    issue(sheet, rownum, "shift_anchor", "El turno requiere una fecha de anclaje.")
        if exported and not project_mode_present:
            bundle.project.pop('mode', None)
        if len(bundle.activities) > LARGE_PROJECT_ACTIVITIES:
            issue("Actividades", 0, "Cantidad",
                  f"El archivo trae {len(bundle.activities):,} actividades; Ganttier se verificó hasta {LARGE_PROJECT_ACTIVITIES:,}. "
                  "La importación y el uso posterior pueden ser lentos o agotar la memoria: considere dividir el proyecto.".replace(',', '.'),
                  "advertencia")
        return bundle
    except (ParseError, BadZipFile, OSError) as exc:
        issue("Libro", 0, "Archivo", f"No se pudo completar la lectura del archivo Excel: {exc}")
        return bundle
    finally:
        workbook.close()
