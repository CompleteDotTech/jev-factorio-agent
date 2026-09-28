"""An explicit receipt opts into native journaling; ordinary gathers do not."""
import json
import hashlib
from importlib.resources import files
from types import SimpleNamespace

import pytest

from jev_factorio.backends.native_factory import NativeFactory
from jev_factorio.backends.native_attachment import prepare_install_command
from jev_factorio.factory_contract import validate_command


def factory(attachment=None):
    calls = []
    backend = SimpleNamespace(_resources={'coal': {'x': 1, 'y': 2}},
                              _native_attachment=attachment, _tools={},
                              _fair=SimpleNamespace(harvest=lambda *args: 3))
    factory = NativeFactory.__new__(NativeFactory)
    factory.backend = backend
    def command(source):
        calls.append(source)
        if 'coal_manual_journal_v1.finish' in source:
            return json.dumps({'status': 'complete', 'coal_before': 0, 'coal_after': 3})
        return ''
    factory.command = command
    return factory, calls


def test_ordinary_gather_keeps_original_command_and_installs_nothing():
    adapter, calls = factory()
    assert adapter.execute('factory_gather', {'resource': 'coal', 'quantity': 3}) == 'Harvested 3 coal'
    assert calls == []


def test_explicit_receipt_uses_prequalified_source_and_finishes_once():
    source = files('jev_factorio').joinpath('lua/coal_manual_journal_v1.lua').read_bytes()
    attachment = {'modules': {'coal_manual_journal_v1': True},
                  'native_installation': {'profile': False,
                      'assets': {'coal_manual_journal_v1': hashlib.sha256(source).hexdigest()}}}
    adapter, calls = factory(attachment)
    params = {'resource': 'coal', 'quantity': 3, 'receipt': 'manual-coal-1'}
    validate_command('factory_gather', params)
    assert adapter.execute('factory_gather', params) == 'Harvested 3 coal'
    assert '.begin("manual-coal-1")' in calls[0]
    assert '.finish("manual-coal-1")' in calls[1]
    assert len(calls) == 2


def test_absent_attachment_cannot_install_during_gather():
    adapter, calls = factory()
    with pytest.raises(RuntimeError, match='qualified native attachment'):
        adapter.execute('factory_gather',
                        {'resource': 'coal', 'quantity': 3, 'receipt': 'manual-coal-1'})
    assert calls == []


def test_journal_installer_has_exact_source_receipt_and_cannot_reinstall_on_attach():
    source = files('jev_factorio').joinpath('lua/coal_manual_journal_v1.lua').read_text()
    wrapped = prepare_install_command(source)
    assert wrapped != source
    assert 'coal_manual_journal_v1' in wrapped
    assert hashlib.sha256(source.encode()).hexdigest() in wrapped
    with pytest.raises(RuntimeError, match='reinstallation'):
        prepare_install_command(source, {'modules': {}})


def test_attached_runtime_without_versioned_journal_rejects_before_gather():
    adapter, calls = factory({'modules': {'coal_manual_journal_v1': False}})
    with pytest.raises(RuntimeError, match='not installed'):
        adapter.execute('factory_gather',
                        {'resource': 'coal', 'quantity': 3, 'receipt': 'manual-coal-1'})
    assert calls == []


def test_journaled_gather_cannot_name_other_resource():
    adapter, calls = factory()
    adapter.backend._resources['iron-ore'] = {'x': 1, 'y': 2}
    with pytest.raises(ValueError, match='coal receipt'):
        adapter.execute('factory_gather',
                        {'resource': 'iron-ore', 'quantity': 3, 'receipt': 'manual-coal-1'})
    assert calls == []


@pytest.mark.parametrize('receipt', ['', 'a' * 129, 'contains space', 'nonascii-\u00e9'])
def test_journal_receipt_contract_rejects_unbounded_or_nonprintable_identity(receipt):
    with pytest.raises(ValueError, match='coal receipt'):
        validate_command('factory_gather',
                         {'resource': 'coal', 'quantity': 1, 'receipt': receipt})
