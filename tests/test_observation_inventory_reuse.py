"""Validate the supported atomic inventory path before omitting a legacy read."""
import json
import sys
from types import SimpleNamespace as NS

import pytest

from jev_factorio.backends.craft_jobs import CraftJobFactory
from jev_factorio.backends.fle import FleBackend


def backend_fixture(monkeypatch, *, craft, consolidated, malformed=False, chest=False):
    monkeypatch.setitem(sys.modules, 'fle.env', NS(
        Prototype=NS(BurnerMiningDrill='drill',WoodenChest='chest'),
        Position=lambda **kwargs:NS(**kwargs)))
    calls=[]
    class Native:
        def observe(self, snapshot):
            calls.append('native_observe')
            snapshot.factory={'craft_job_inventory':{'tick': snapshot.tick, 'items': {'coal':8}}}
            if malformed: snapshot.factory['craft_job_inventory']['tick']-=1
            return snapshot
    native=Native()
    if craft:
        wrapped=CraftJobFactory.__new__(CraftJobFactory);wrapped.native=native;native=wrapped
    drill=NS(name='burner-mining-drill',drop_position=NS(x=2,y=0),fuel={'coal':3},status=NS(value='working'))
    output=NS(name='wooden-chest',position=NS(x=2,y=0))
    class Tools:
        def inspect_inventory(self,*args):
            calls.append('chest_inventory' if args else 'actor_inventory')
            return {'iron-ore':5} if args else {'coal':8}
        def get_entities(self,*args):
            calls.append('entities')
            return [drill,output] if chest else []
    class Fair:
        def call(self,name,*args):
            calls.append(name)
            return {} if name=='observe' else {'name':args[0],'position':{'x':1,'y':1},'surface_index':1}
    backend=FleBackend()
    backend.consolidated_observations=consolidated
    backend._factory=native;backend._fair=Fair()
    backend._instance=NS(namespace=Tools(),rcon_client=NS(send_command=lambda command:
        json.dumps({'tick':10,'session_id':'fixture','position':[0,0]})))
    return backend,calls


@pytest.mark.parametrize('craft',[False,True])
@pytest.mark.parametrize('consolidated',[False,True])
def test_combination_retains_inventory_and_removes_only_supported_duplicate(monkeypatch,craft,consolidated):
    backend,calls=backend_fixture(monkeypatch,craft=craft,consolidated=consolidated)
    observed=backend.observe()
    assert observed.inventory=={'coal':8}
    assert calls.count('actor_inventory')==(0 if craft else 1)
    assert calls.count('observe')==1 and calls.count('native_observe')==1
    assert calls.count('entities')==1  # Broad FLE entity conversion is not claimed removed by this patch.


def test_bad_atomic_inventory_does_not_silently_fall_back_or_expose_placeholder(monkeypatch):
    backend,calls=backend_fixture(monkeypatch,craft=True,consolidated=True,malformed=True)
    with pytest.raises(ValueError,match='atomic crafting inventory'): backend.observe()
    assert 'actor_inventory' not in calls


def test_bootstrap_chest_inventory_still_read(monkeypatch):
    backend,calls=backend_fixture(monkeypatch,craft=True,consolidated=True,chest=True)
    observed=backend.observe()
    assert observed.drill_output_connected is True
    assert observed.iron_ore_collected==5 and observed.drill_status=='working'
    assert calls.count('chest_inventory')==1 and 'actor_inventory' not in calls


def test_swallowed_atomic_adapter_does_not_return_empty_inventory(monkeypatch):
    backend,calls=backend_fixture(monkeypatch,craft=True,consolidated=False)
    # A broken wrapper that bypasses its atomic reader must fail the backend's
    # completion marker rather than authorizing a snapshot with placeholder stock.
    backend._factory.observe=lambda snapshot:snapshot
    with pytest.raises(ValueError,match='Atomic inventory'): backend.observe()
