"""Seed pre-R3/R4 reservations for migration/backward-compatibility tests only."""
from invest_agent.trading.contracts import digest, encode
from invest_agent.trading.store import now


def seed_legacy_reservation(store, event):
    assert event['kind'] == 'reserve'
    with store.db:
        store.db.execute('INSERT INTO plan_events VALUES (?,?,?,?,?,?,?)',
                         (event['event_id'], event['plan_id'], 'reserve',
                          encode(event), digest(event), None, now()))
