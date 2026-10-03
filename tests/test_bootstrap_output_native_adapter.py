"""Preflight piggybacks the existing approach lookup before actor movement."""
import json
import sys
from types import SimpleNamespace

import pytest

from jev_factorio.bootstrap_output import ROLE
from jev_factorio.backends.native_factory import NativeFactory
from test_bootstrap_output_lua import runtime


@pytest.mark.parametrize('mutation',[
    None, 'chest.unit_number=999', "jev_fle_runtime.bootstrap_output_v1.transfer_pending={receipt='unknown'}",
    'chest_stock=0',
])
def test_real_lua_native_preflight_rejects_stale_or_pending_source_before_approach(monkeypatch,mutation):
    lua=runtime();approaches=[];commands=[]
    lua.execute('storage=jev_fle_runtime;rcon={print=function() end};helpers={table_to_json=function() return "{}" end}')
    if mutation:lua.execute(mutation)
    native=NativeFactory.__new__(NativeFactory)
    native.backend=SimpleNamespace(_fair=SimpleNamespace(approach=lambda *args:approaches.append(args)))
    def command(script):
        commands.append(script);lua.execute(script)
        return json.dumps(dict(name='wooden-chest',position=dict(x=48.5,y=-82.5)))
    native.command=command
    monkeypatch.setitem(sys.modules,'fle.env',SimpleNamespace(Position=lambda **kw:SimpleNamespace(**kw)))
    parameters=dict(role=ROLE,item='iron-ore',quantity=20,
        receipt='100:factory_extract:bootstrap-output:iron-ore:iron-ore')
    if mutation:
        with pytest.raises(Exception):native.approach_role(ROLE,bootstrap_parameters=parameters)
        assert approaches==[]
    else:
        native.approach_role(ROLE,bootstrap_parameters=parameters)
        assert len(approaches)==1
    assert len(commands)==1 # No additional preflight RPC is introduced.
