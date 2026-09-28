"""A resumed adapter must inspect and reuse the complete native installation."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jev_factorio.backends.fair_actions import FairActions
from jev_factorio.backends.native_attachment import (
    PINNED_ASSETS, PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE, PROBE,
    readback, require_asset,
)
from jev_factorio.backends.output_buffers import OutputBufferFactory
from jev_factorio.backends.input_routes import InputRouteFactory
from jev_factorio.backends.mining_outposts import MiningOutpostFactory


def qualified():
    return {'schema': 1, 'qualified': True, 'session_id': 'synthetic-session',
            'actor_unit': 17, 'modules': dict.fromkeys(PINNED_ASSETS, True),
            'solid_intents': [], 'coal_targets': [], 'coal_admission_evidence': False}


def test_preflight_is_fixed_read_only_query_and_rejects_partial_chain(tmp_path, monkeypatch):
    class Client:
        def __init__(self, payload):
            self.payload = payload
            self.sent = []

        def send_command(self, command):
            self.sent.append(command)
            return json.dumps(self.payload)

    receipt = {'schema': 'jev.native-attachment.v1',
               'session_id': 'synthetic-session', 'actor_unit': 17,
               'installed_source_commit': PINNED_SOURCE_COMMIT,
               'installed_source_tree': PINNED_SOURCE_TREE,
               'installed_assets': dict(PINNED_ASSETS)}
    path = tmp_path / 'attachment.json'
    path.write_text(json.dumps(receipt))
    path.chmod(0o600)
    monkeypatch.setenv('JEV_NATIVE_ATTACHMENT_RECEIPT', str(path))
    client = Client(qualified())
    assert readback(client)['session_id'] == 'synthetic-session'
    assert client.sent == ['/sc ' + PROBE]
    assert 'script.on_event' not in PROBE and 'script.on_nth_tick' not in PROBE
    assert 'fair.bind(' not in PROBE and 'campaign.observe(' not in PROBE
    assert 'c.observe==i.observer and c.transfer==i.transfer' in PROBE
    assert 'c.observe==b.observer and c.transfer==b.transfer' in PROBE
    assert 'c.observe==j.observe_wrapper and c.transfer==l.transfer' in PROBE
    for corruption in ('qualified', 'missing_module', 'wrong_schema'):
        row = qualified()
        if corruption == 'qualified':
            row['qualified'] = False
        elif corruption == 'missing_module':
            row['modules'].pop('coal_supply')
        else:
            row['schema'] = 2
        with pytest.raises(RuntimeError, match='requires reconciliation'):
            readback(Client(row))
    receipt['actor_unit'] = 18
    path.write_text(json.dumps(receipt))
    with pytest.raises(RuntimeError, match='does not match'):
        readback(Client(qualified()))
    monkeypatch.delenv('JEV_NATIVE_ATTACHMENT_RECEIPT')
    with pytest.raises(RuntimeError, match='requires a source-bound'):
        readback(Client(qualified()))


def test_resume_skips_fair_bind_and_outer_lua_reinstallation():
    backend = SimpleNamespace(_native_attachment=qualified())
    backend._native_attachment['solid_intents'] = []
    fair = FairActions(backend)
    assert fair.backend is backend

    class Native:
        def __init__(self):
            self.backend = backend
            self.commands = []

        def command(self, script):
            self.commands.append(script)
            raise AssertionError('Native installer or mutator ran during checked reattach')

    base = Native()
    assert OutputBufferFactory(base).native is base
    assert InputRouteFactory(base).native is base
    assert MiningOutpostFactory(base).native is base
    assert base.commands == []


def test_source_change_or_missing_capability_cannot_reattach(monkeypatch):
    attachment = qualified()
    assert require_asset(attachment, 'fair_actions') is True
    attachment['modules']['fair_actions'] = False
    with pytest.raises(RuntimeError, match='not installed'):
        require_asset(attachment, 'fair_actions')
    attachment['modules']['fair_actions'] = True
    monkeypatch.setitem(PINNED_ASSETS, 'fair_actions', '0' * 64)
    with pytest.raises(RuntimeError, match='Lua source differs'):
        require_asset(attachment, 'fair_actions')
