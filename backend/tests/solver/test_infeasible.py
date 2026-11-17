from datetime import timedelta

import pytest

from ganttier.domain import ObjectiveWeights, OptimizeOptions, Relation
from ganttier.engine import optimize


@pytest.mark.parametrize('cause,fragment', [('skill', 'capacidad'), ('zero', 'capacidad'), ('window', 'ventana'), ('mfo_chain', 'precedencia')])
def test_infeasible_has_spanish_diagnosis(case, cause, fragment):
    p = case()
    if cause == 'skill':
        p.resources = []
    elif cause == 'zero':
        p.resources[0].max_units = 0
    elif cause == 'window':
        p.activities[0].duration_days = 3
        p.activities[0].window_hard = True
        p.activities[0].window_start = p.activities[0].window_end = p.horizon_start
    else:
        p.activities[0].duration_days = 4
        p.activities[1].constraint_type = 'MFO'
        p.activities[1].constraint_date = p.horizon_start + timedelta(days=1)
        p.relations = [Relation(1, 1, 1, 2)]
    result = optimize(p, ObjectiveWeights(makespan=1, window=0), OptimizeOptions(method='heuristic', time_limit_s=.5))
    assert result.status == 'infactible'
    assert not result.starts and not result.finishes and result.after is None
    assert fragment in ' '.join(result.warnings).lower()


def test_cycles_are_data_errors(case):
    p = case()
    p.relations = [Relation(1, 1, 1, 2), Relation(2, 1, 2, 1)]
    with pytest.raises(ValueError, match='Ciclo'):
        optimize(p, ObjectiveWeights(), OptimizeOptions())


def test_zero_work_milestone_does_not_require_capacity(case):
    p = case(n=1, duration=0, hours=0)
    p.resources = []
    result = optimize(p, ObjectiveWeights(makespan=1, window=0), OptimizeOptions(method='heuristic'))
    assert result.status != 'infactible', result.warnings
    assert result.starts[1] == result.finishes[1] == p.horizon_start
