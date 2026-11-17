"""La precarga de H1b conserva 100k sin competir con el Gantt masivo de H2."""
import pytest


@pytest.mark.parametrize('total,precalcula', [(100000, True), (150000, True), (250003, False)])
def test_precarga_respeta_capacidad_de_cache(qapp, monkeypatch, total, precalcula):
    from ganttier.gui.pages import control
    jobs=[];calls=[];plots=[]
    class Client:
        def get(self,path,**kwargs):
            calls.append((path,kwargs))
            return {'total':total} if path.endswith('/activities') else {'evm':{},'scurve':{}}
    monkeypatch.setattr(control,'run_task',lambda fn,**kwargs:jobs.append(fn))
    monkeypatch.setattr(control,'_prepare_plot_runtime',lambda:plots.append(True))
    control.prefetch_dashboard(Client(),7)
    assert len(jobs)==1
    jobs[0]()
    assert calls[0]==('/projects/7/control/activities',{'params':{'limit':1,'scope':'all'}})
    assert any(path.endswith('/dashboard') for path,_ in calls)==precalcula
    assert bool(plots)==precalcula
