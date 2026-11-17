"""Lecturas de la ventana de avance; no carga el programa completo en Qt."""
from datetime import date

from .. import repo


START = "COALESCE(planned_start,early_start,actual_start,'9999-12-31')"
DATED = "COALESCE(planned_start,early_start,actual_start) IS NOT NULL"
FINISH = "COALESCE(planned_finish,early_finish,actual_finish,planned_start,early_start,actual_start)"


def install_indexes(conn):
    """Índice auxiliar idempotente, compatible con el esquema v6 existente."""
    conn.execute(f'CREATE INDEX IF NOT EXISTS ix_control_page ON activities(project_id,{START},id)')
    conn.execute(f'''CREATE INDEX IF NOT EXISTS ix_control_open
        ON activities(project_id,{START},id) WHERE percent_complete<100 AND {DATED}''')


def activities_page(conn, pid, *, offset=0, limit=200, scope='open', q=''):
    project = repo.get(conn, 'projects', pid)
    if offset < 0 or not 1 <= limit <= 500 or scope not in {'all', 'open', 'late'}:
        raise ValueError('Paginación o filtro de avance inválidos.')
    cut = (project.data_date or date.today()).isoformat()
    clauses, args = ['project_id=?'], [pid]
    if scope == 'open':
        clauses += ['percent_complete<100', DATED, f'{START}<=?']
        args += [cut]
    elif scope == 'late':
        clauses += ['percent_complete<100', f'{FINISH}<=?']
        args += [cut]
    if q.strip():
        # casefold reproduce la búsqueda anterior (incluye Unicode y ß/ss).
        conn.create_function('control_casefold', 1, lambda v: v.casefold(), deterministic=True)
        clauses += ["instr(control_casefold(COALESCE(code,'') || ' ' || COALESCE(name,'') || ' ' || COALESCE(location,'') || ' ' || COALESCE(required_skill,'')),?)>0"]
        args += [q.strip().casefold()]
    where = ' AND '.join(clauses)
    total = conn.execute(f'SELECT COUNT(*) FROM activities WHERE {where}', args).fetchone()[0]
    offset = min(offset, max(0, (total - 1) // limit) * limit)
    rows = conn.execute(f'''SELECT id,code,name,
        COALESCE(planned_start,early_start,actual_start) AS start,
        COALESCE(planned_finish,early_finish,actual_finish) AS finish,
        work_hours,percent_complete AS pct,actual_start,actual_finish
        FROM activities WHERE {where} ORDER BY {START},id LIMIT ? OFFSET ?''', [*args,limit,offset])
    return dict(rows=[dict(row) for row in rows], total=total, offset=offset, limit=limit, data_date=cut)
