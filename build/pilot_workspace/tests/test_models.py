"""Models must survive a round trip through sqlite."""

from app import store
from app.models import MODELS


def test_every_model_round_trips():
    record = {'reference': 'x', 'status': 'x', 'quantity': 1}
    saved = store.save('analytics_surface', record)
    assert saved['id'] is not None, 'no id assigned for analytics_surface'
    fetched = store.get('analytics_surface', saved['id'])
    assert fetched is not None, 'analytics_surface did not persist'
    for key, value in record.items():
        assert fetched[key] == value, (key, fetched[key], value)
    assert any(r['id'] == saved['id'] for r in store.list_all('analytics_surface'))
    record = {'reference': 'x', 'status': 'x', 'quantity': 1}
    saved = store.save('dashboard_surface', record)
    assert saved['id'] is not None, 'no id assigned for dashboard_surface'
    fetched = store.get('dashboard_surface', saved['id'])
    assert fetched is not None, 'dashboard_surface did not persist'
    for key, value in record.items():
        assert fetched[key] == value, (key, fetched[key], value)
    assert any(r['id'] == saved['id'] for r in store.list_all('dashboard_surface'))


def test_models_expose_their_fields():
    assert MODELS, 'no models were generated'
    for cap_id, cls in MODELS.items():
        instance = cls.from_dict({})
        assert instance.to_dict()['id'] is None
        assert cls.FIELDS, cap_id
