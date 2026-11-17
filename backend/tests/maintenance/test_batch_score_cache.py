"""La reutilización conserva fechas, asignaciones y objetivo con cargas compartidas."""
from dataclasses import replace
from datetime import date,timedelta
import pytest
from ganttier import domain as d
from ganttier.calendars import WorkCalendar
from ganttier.maintenance.generator import generate_occurrences
from ganttier.maintenance.leveling import level_periodic,_TeamLeveler

@pytest.mark.parametrize('strict',[False,True])
@pytest.mark.parametrize('period',['day','week'])
def test_cache_keeps_exact_schedule(monkeypatch,strict,period):
    first,last=date(2027,1,1),date(2027,3,31)
    calendars={i:WorkCalendar(d.Calendar(i,str(i),shift_pattern='7x7',shift_anchor=first+timedelta(days=(i-1)*7)),first,last) for i in [1,2]}
    resources=[d.Resource(i,str(i),kind='cuadrilla',calendar_id=i,max_units=3,is_work_team=True,project_id=1) for i in [1,2]]
    tasks=[d.PeriodicTask(i,1,str(i),'Prueba',first_due=first,quantity_total=8500,batch_size=2000,productivity_day_team=1000,unit='u',team_ids=[1,2],batch_continuous_in_shift=strict,requires_outage=True,location='Planta') for i in [1,2]]
    rows=generate_occurrences(tasks,first,last,calendars,1,resources=resources)
    args=(rows,resources,calendars,d.ObjectiveWeights(level=1,window=1,makespan=1,cost=1),d.OptimizeOptions(level_period=period,group_outages=True,allow_overtime=False))
    cached=level_periodic(*args,horizon_start=first,horizon_end=last)
    original=_TeamLeveler._batch_score
    def fresh(self,i,c,member,cache):
        return original(self,i,c,member,{})
    monkeypatch.setattr(_TeamLeveler,'_batch_score',fresh)
    uncached=level_periodic(*args,horizon_start=first,horizon_end=last)
    assert (cached.starts,cached.finishes,cached.worker_assignment,cached.after,cached.status)==(uncached.starts,uncached.finishes,uncached.worker_assignment,uncached.after,uncached.status)
