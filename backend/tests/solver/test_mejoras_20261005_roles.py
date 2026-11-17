from test_role_api import client, mini_case
from role_case import ok


def test_nominal_display_uses_names_and_hides_obsolete_rows(client):
    path, body = mini_case(client)
    body['apply'] = True
    result = ok(client.post(path+'/schedule/solve', json=body))
    assert result['feasible'] and result['applied']
    state = ok(client.get(path+'/schedule/role-assignments'))
    assert state['valid']
    assert len(state['display_rows']) == len(state['role_assignment']) == 1
    row = state['display_rows'][0]
    assert row['activity_code'] == 'A' and row['resource_name'] == 'Ana'
    assert row['source_assignment_id'] < 0  # Demand derived from activity.
    ok(client.put('/api/activities/'+str(row['activity_id']), json={'name': 'Cambiada'}))
    state = ok(client.get(path+'/schedule/role-assignments'))
    assert state['applied'] and not state['valid']
    assert state['display_rows'] == []
