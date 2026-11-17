from dataclasses import replace
from datetime import timedelta
import json

import pytest

from ganttier.domain import Assignment, Calendar, CalendarException, Relation, Resource, to_dict
from ganttier.calendars import WorkCalendar
from ganttier.engine.verify import verify_schedule


def types(report):
    return {v['type'] for v in report.violations}


def test_valid_and_serializable(case):
    p = case()
    s = {1: p.horizon_start, 2: p.horizon_start + timedelta(days=1)}
    report = verify_schedule(p, s, s)
    assert report.feasible and report.stats['scheduled'] == 2
    json.dumps(to_dict(report), allow_nan=False)


@pytest.mark.parametrize('kind', ['MSO', 'MFO', 'SNET', 'FNET', 'hard_window', 'data_date', 'duration', 'calendar', 'missing_dates', 'locked'])
def test_each_date_violation(case, kind):
    p = case(n=1)
    a, day = p.activities[0], p.horizon_start
    s, f = {1: day}, {1: day}
    if kind in {'MSO', 'MFO', 'SNET', 'FNET'}:
        a.constraint_type, a.constraint_date = kind, day + timedelta(days=1)
    elif kind == 'hard_window':
        a.window_hard, a.window_start = True, day + timedelta(days=1)
    elif kind == 'data_date':
        p.data_date = day + timedelta(days=1)
    elif kind == 'duration':
        a.duration_days = 2
    elif kind == 'calendar':
        s[1] = f[1] = day + timedelta(days=6)
    elif kind == 'missing_dates':
        f = {}
    elif kind == 'locked':
        a.locked, a.planned_start = True, day + timedelta(days=1)
    assert kind in types(verify_schedule(p, s, f))


@pytest.mark.parametrize('kind', ['SNLT', 'FNLT', 'soft_window'])
def test_soft_constraints_are_warnings(case, kind):
    p = case(n=1)
    a, day = p.activities[0], p.horizon_start
    if kind == 'soft_window':
        a.window_end = day
    else:
        a.constraint_type, a.constraint_date = kind, day
    s = {1: day + timedelta(days=1)}
    report = verify_schedule(p, s, s)
    assert report.feasible and kind in {w['type'] for w in report.warnings}


@pytest.mark.parametrize('kind', ['FS', 'SS', 'FF', 'SF'])
@pytest.mark.parametrize('lag', [-2, 0, 2])
def test_relations_across_weekend_and_calendars(case, kind, lag):
    p = case(units=3)
    p.activities[0].calendar_id = 2
    p.relations = [Relation(1, 1, 1, 2, kind, lag)]
    friday = p.horizon_start + timedelta(days=4)
    # A1 is Friday, successor uses L-V. FS0 is Monday; other events Friday.
    base = 5 if kind == 'FS' else 4
    workdays = [p.horizon_start + timedelta(days=i) for i in range(20) if (p.horizon_start + timedelta(days=i)).weekday() < 5]
    required = workdays[base + lag]
    good = {1: friday, 2: required}
    assert verify_schedule(p, good, good).feasible
    bad = {1: friday, 2: workdays[base + lag - 1]}
    assert 'precedence' in types(verify_schedule(p, bad, bad))


def test_resource_capacity_and_overtime(case):
    p = case(n=1, hours=10)
    p.assignments = [Assignment(1, 1, 1, work_hours=10)]
    s = {1: p.horizon_start}
    assert 'resource_capacity' in types(verify_schedule(p, s, s))
    p.resources[0].max_overtime_hours_day = 2
    assert verify_schedule(p, s, s).feasible


def test_reduced_calendar_capacity_and_proportional_hours(case):
    p = case(n=1, duration=2, hours=12)
    cal = Calendar(3, 'Reducido', holidays_country=None, exceptions=[CalendarException(p.horizon_start, True, 4)])
    p.calendars[3] = WorkCalendar(cal, p.horizon_start, p.horizon_end)
    p.activities[0].calendar_id = p.resources[0].calendar_id = 3
    p.assignments = [Assignment(1, 1, 1, work_hours=12)]
    s, f = {1: p.horizon_start}, {1: p.horizon_start + timedelta(days=1)}
    assert verify_schedule(p, s, f).feasible
    p.activities[0].calendar_id = 1  # uniform 6 HH exceeds the resource's 4 HH Monday
    assert 'resource_capacity' in types(verify_schedule(p, s, f))


def test_multiskill_resource_cannot_be_counted_twice(case):
    p = case()
    p.resources[0].skills += ['mecanico']
    p.activities[1].required_skill = 'mecanico'
    s = {1: p.horizon_start, 2: p.horizon_start}
    assert 'skill_capacity' in types(verify_schedule(p, s, s))
    p.resources.append(Resource(2, 'Beto', skills=['electrico']))
    assert verify_schedule(p, s, s).feasible  # requires a residual reassignment


@pytest.mark.parametrize('kind', ['worker_missing', 'skill', 'worker_calendar', 'crew', 'worker'])
def test_maintenance_workers(case, kind):
    p = case(n=1, mode='mantenimiento')
    day = p.horizon_start
    worker, crews = {1: 1}, {1: [1]}
    if kind == 'worker_missing':
        worker, crews = {}, {}
    elif kind == 'skill':
        p.resources[0].skills = ['mecanico']
    elif kind == 'worker_calendar':
        p.calendars[3] = WorkCalendar(Calendar(3, 'Libre lunes', workweek='0111110', holidays_country=None), day, p.horizon_end)
        p.resources[0].calendar_id = 3
    elif kind == 'crew':
        p.activities[0].crew_size = 2
    else:
        worker, crews = {1: 99}, {1: [99]}
    assert kind in types(verify_schedule(p, {1: day}, {1: day}, worker_assignment=worker, crews=crews))


def test_split_crew_and_completed_progress(case):
    p = case(n=1, mode='mantenimiento', hours=16)
    p.activities[0].crew_size = 2
    p.resources.append(replace(p.resources[0], id=2, name='Beto'))
    s = {1: p.horizon_start}
    assert verify_schedule(p, s, s, worker_assignment={1: 1}, crews={1: [1, 2]}).feasible
    assert 'crew' in types(verify_schedule(p, s, s, worker_assignment={1: 1}, crews={1: [1, 1]}))
    a = p.activities[0]
    a.actual_start = a.actual_finish = p.horizon_start
    a.percent_complete, a.status = 100, 'terminada'
    assert verify_schedule(p, s, s).feasible


def test_progress_override_checks_remaining_work(case):
    p = case(n=2, duration=4, hours=32)
    a = p.activities[1]
    a.actual_start, a.percent_complete, a.remaining_duration = p.horizon_start, 50, 2
    p.data_date = p.horizon_start + timedelta(days=7)
    p.relations = [Relation(1, 1, 1, 2)]
    # Corte al final del día: el remanente (2 días) de A2 ocupa los dos días siguientes al corte.
    s = {1: p.data_date + timedelta(days=3), 2: a.actual_start}
    f = {1: p.calendars[1].add_workdays(s[1], 4), 2: p.data_date + timedelta(days=2)}
    report = verify_schedule(p, s, f)
    assert report.feasible, report.violations
    assert any(w['type'] == 'progress_override' for w in report.warnings)
    f[2] = p.data_date + timedelta(days=1)
    assert 'duration' in types(verify_schedule(p, s, f))
