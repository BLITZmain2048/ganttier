"""La duración cuenta solo turnos; continuidad estricta es una opción explícita."""
from datetime import date, timedelta
from dataclasses import replace, asdict
import pytest
from ganttier import db, repo, services, teams, domain as d
from ganttier.engine.verify import verify_schedule
from ganttier.excel_io.exporter import export_project
from ganttier.excel_io.importer import parse_workbook

@pytest.fixture
def scenario(tmp_path,monkeypatch):
    monkeypatch.setattr(services,'DATA_DIR',tmp_path/'datos')
    c=db.connect(tmp_path/'test.db'); db.initialize(c)
    first,last=date(2027,1,1),date(2027,12,31)
    p=repo.create(c,'projects',dict(name='Paneles',mode='mantenimiento',start_date=first,end_date=last,data_date=first,calendar_id=1))
    ids=[teams.save_team(c,p.id,dict(name='Equipo '+x,regime='7x7',cycle_start=first+timedelta(days=i*7),members=1,hours_day=8,holidays_work=True))['id'] for i,x in enumerate('AB')]
    t=repo.create(c,'periodic_tasks',dict(project_id=p.id,code='P',name='Paneles',first_due=first,quantity_total=28000,unit='paneles',batch_size=2000,productivity_day_team=1000,team_ids=ids))
    yield c,p,t
    c.close()

@pytest.mark.parametrize('strict',[False,True])
def test_both_modes_apply_and_excel_roundtrip(scenario,tmp_path,strict):
    c,p,t=scenario
    assert t.batch_continuous_in_shift is False
    repo.update(c,'periodic_tasks',t.id,dict(batch_continuous_in_shift=strict))
    path=tmp_path/'plantilla.xlsx'; export_project(repo.export_bundle(c,p.id),path)
    parsed=parse_workbook(path); assert not parsed.has_errors
    assert parsed.periodic_tasks[0]['batch_continuous_in_shift'] is strict
    result=services.generate_maintenance(c,p.id,p.start_date,p.end_date)
    assert result['applied'] is (not strict),result
    if strict:
        assert result['status']=='infactible'
        assert repo.list_entities(c,'activities',p.id)==[]
        return
    problem=services.build_problem(c,p.id)
    starts={a.id:a.planned_start for a in problem.activities}; ends={a.id:a.planned_finish for a in problem.activities}
    assert len(starts)==168
    assert verify_schedule(problem,starts,ends).feasible
    crossing=[]
    for a in problem.activities:
        cal=problem.calendars[a.calendar_id]
        days=[date.fromordinal(n) for n in range(starts[a.id].toordinal(),ends[a.id].toordinal()+1) if cal.is_workday(date.fromordinal(n))]
        assert len(days)==a.duration_days==2
        assert days[0]==starts[a.id] and days[-1]==ends[a.id]
        if (ends[a.id]-starts[a.id]).days>1: crossing.append(a)
    assert crossing
    a=crossing[0]; original=ends[a.id]; ends[a.id]=original+timedelta(days=1)
    assert not verify_schedule(problem,starts,ends).feasible
    ends[a.id]=original
    problem.periodic_tasks=[replace(x,batch_continuous_in_shift=True) for x in problem.periodic_tasks]
    assert not verify_schedule(problem,starts,ends).feasible

def test_long_batch_can_span_shifts_by_default(scenario):
    c,p,t=scenario
    repo.update(c,'periodic_tasks',t.id,dict(asdict(t),quantity_total=18000,batch_size=9000))
    assert services.generate_maintenance(c,p.id,p.start_date,p.end_date)['applied']
    repo.update(c,'periodic_tasks',t.id,dict(batch_continuous_in_shift=True))
    with pytest.raises(ValueError,match='bloque'):
        services.generate_maintenance(c,p.id,p.start_date,p.end_date)
