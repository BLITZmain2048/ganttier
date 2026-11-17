"""Regresiones propuestas para tests/solver/test_role_blocks.py."""
from dataclasses import replace
from datetime import date, timedelta
import pytest
pytest.importorskip('ortools')
from ganttier.domain import Calendar, CalendarException, Project, Resource, Activity, Assignment, Relation, ScheduleProblem, OptimizeOptions
from ganttier.calendars import WorkCalendar
from ganttier.engine.role_blocks import optimize_role_blocks

D0=date(2027,1,1)

def problem():
    cal=Calendar(1,'bloques',workweek='1111111',hours_per_day=4,holidays_country=None)
    p=Project(1,'test',start_date=D0,end_date=D0+timedelta(days=3),calendar_id=1)
    a=Activity(1,1,'A','roles',duration_days=1,calendar_id=1,work_hours=8)
    resources=[Resource(1,'Ana',skills=['C','I'],capacity_hours_day=4,calendar_id=1,cost_hour=35),
               Resource(2,'Elena',skills=['I'],capacity_hours_day=4,calendar_id=1,cost_hour=27.5)]
    return ScheduleProblem(p,[a],[],resources,[Assignment(1,1,skill='C'),Assignment(2,1,skill='I')],
                           {1:WorkCalendar(cal,D0,p.end_date)},1,D0,p.end_date)

def solve(p):
    return optimize_role_blocks(p,OptimizeOptions(allow_overtime=False,time_limit_s=5),target_code='A')

def test_two_distinct_roles_and_cost():
    r=solve(problem())
    assert r['status']=='optimo' and r['primary_finish_block']==1
    assert {(x['skill'],x['resource_id']) for x in r['role_assignment']}=={('C',1),('I',2)}
    assert r['personnel_cost']==250 and r['verification']['feasible']

def test_one_person_cannot_fill_two_roles():
    p=problem(); p.resources=p.resources[:1]
    r=solve(p)
    assert not r['feasible'] and r['infeasibility']=='demostrada'

def test_absence_zero_blocks_and_end_is_excluded():
    p=problem()
    cal=replace(p.calendars[1].cal,id=2,exceptions=[CalendarException(D0,False,0),CalendarException(D0+timedelta(days=1),False,0)])
    p.calendars[2]=WorkCalendar(cal,D0,p.horizon_end)
    p.resources[1]=replace(p.resources[1],calendar_id=2)
    r=solve(p)
    assert r['blocks'][1]=={'start':2,'finish':3}

def test_worker_is_constant_for_whole_activity():
    p=problem(); p.activities=[replace(p.activities[0],duration_days=2,work_hours=8)]
    p.assignments=[Assignment(1,1,skill='I')]
    p.horizon_end=D0+timedelta(days=1)
    for rid,off in [(1,0),(2,1)]:
        cal=replace(p.calendars[1].cal,id=rid+1,exceptions=[CalendarException(D0+timedelta(days=off),False,0)])
        p.calendars[rid+1]=WorkCalendar(cal,D0,p.horizon_end)
        p.resources[rid-1]=replace(p.resources[rid-1],calendar_id=rid+1)
    assert not solve(p)['feasible']

@pytest.mark.parametrize('units,feasible',[(2,True),(3,False)])
def test_equipment_units(units,feasible):
    p=problem()
    p.resources.append(Resource(3,'HE',kind='equipo',max_units=2,capacity_hours_day=4,calendar_id=1))
    p.assignments.append(Assignment(3,1,resource_id=3,units=units,work_hours=units*4))
    assert solve(p)['feasible']==feasible

def test_equipment_shared_across_activities():
    p=problem(); p.activities=[replace(p.activities[0],work_hours=0),Activity(2,1,'B','B',duration_days=1)]
    p.resources=[Resource(3,'HE',kind='equipo',max_units=2,capacity_hours_day=4,calendar_id=1)]
    p.assignments=[Assignment(1,1,resource_id=3,units=2),Assignment(2,2,resource_id=3,units=1)]
    r=optimize_role_blocks(p,OptimizeOptions(allow_overtime=False,time_limit_s=5))
    assert r['primary_finish_block']==2

def test_partial_hours_rejected():
    p=problem(); p.assignments[0]=replace(p.assignments[0],work_hours=2)
    with pytest.raises(ValueError,match='HH incompatible'): solve(p)

def test_civil_holiday_rejected():
    p=problem(); cal=replace(p.calendars[1].cal,exceptions=[CalendarException(D0,False,0)])
    p.calendars[1]=WorkCalendar(cal,D0,p.horizon_end)
    with pytest.raises(ValueError,match='feriados'): solve(p)

def test_nominal_wrong_skill_rejected_by_model():
    p=problem(); p.assignments[0]=replace(p.assignments[0],resource_id=2)
    assert not solve(p)['feasible']

def test_progress_rejected():
    p=problem(); p.activities[0]=replace(p.activities[0],percent_complete=10)
    with pytest.raises(ValueError,match='avance'): solve(p)

def test_milestone_at_terminal_boundary():
    p=problem(); p.horizon_end=D0
    p.activities.append(Activity(2,1,'FIN','fin',duration_days=0))
    p.relations=[Relation(1,1,1,2)]
    r=optimize_role_blocks(p,OptimizeOptions(allow_overtime=False,time_limit_s=5),target_code='FIN')
    assert r['feasible'] and r['blocks'][2]=={'start':1,'finish':1}


def test_earlier_finish_wins_even_if_staff_is_more_expensive():
    p = problem()
    p.assignments = [Assignment(1, 1, skill='I')]
    p.activities = [replace(p.activities[0], work_hours=4)]
    # Elena is cheaper, but unavailable in the first block. Ana must win.
    cal = replace(p.calendars[1].cal, id=2, exceptions=[CalendarException(D0, False, 0)])
    p.calendars[2] = WorkCalendar(cal, D0, p.horizon_end)
    p.resources[1] = replace(p.resources[1], calendar_id=2)
    result = solve(p)
    assert result['primary_finish_block'] == 1 and result['personnel_cost'] == 140
    assert result['role_assignment'][0]['resource_id'] == 1


def test_cost_selects_cheapest_person_with_same_finish():
    p = problem()
    p.assignments = [Assignment(1, 1, skill='I')]
    p.activities = [replace(p.activities[0], work_hours=4)]
    result = solve(p)
    assert result['primary_finish_block'] == 1 and result['personnel_cost'] == 110
    assert result['role_assignment'][0]['resource_id'] == 2


def test_locked_dates_and_lag_are_preserved():
    p = problem()
    p.activities[0] = replace(p.activities[0], locked=True, planned_start=D0+timedelta(days=1), planned_finish=D0+timedelta(days=1))
    p.activities.append(Activity(2, 1, 'FIN', 'fin', duration_days=0))
    p.relations = [Relation(1, 1, 1, 2, lag_days=1)]
    result = optimize_role_blocks(p, OptimizeOptions(allow_overtime=False), target_code='FIN')
    assert result['blocks'][1] == dict(start=1, finish=2)
    assert result['primary_finish_block'] == 3


def test_unproven_primary_does_not_claim_lexicographic_optimum(monkeypatch):
    from ortools.sat.python import cp_model
    original = cp_model.CpSolver.solve
    calls = []

    def incumbent_only(self, model, *args, **kwargs):
        status = original(self, model, *args, **kwargs)
        assert status == cp_model.OPTIMAL
        calls.append(status)
        return cp_model.FEASIBLE

    monkeypatch.setattr(cp_model.CpSolver, 'solve', incumbent_only)
    result = solve(problem())
    assert result['feasible'] and result['status'] == 'factible'
    assert result['stage1'] == 'FEASIBLE' and result['stage2'] == 'NO_EJECUTADA'
    assert len(calls) == 1 and result['personnel_cost_bound'] is None
