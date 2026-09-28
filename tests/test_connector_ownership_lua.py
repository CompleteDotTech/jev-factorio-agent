"""Connector payment receipts on a Lua API double; no engine physics simulated."""
from pathlib import Path

import pytest
from lupa.lua52 import LuaRuntime, LuaError


SOURCE = (Path(__file__).resolve().parents[1] /
          "src/jev_factorio/lua/connector_ownership.lua").read_text()


def fixture():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        game={tick=100}
        defines={direction={north=0},build_check_type={manual=1}}
        local force={index=2};local surface={index=3}
        local character={unit_number=4}
        local player={force=force,surface=surface,character=character}
        local entities={}
        function surface.find_entity(name,p) return entities[name..":"..p.x..":"..p.y] end
        function surface.can_place_entity(q)
            return surface.find_entity(q.name,q.position)==nil
        end
        local source={valid=true,unit_number=10,force=force,surface=surface}
        local target={valid=true,unit_number=11,force=force,surface=surface}
        storage={jev_session_id="session",campaign={entities={source=source,target=target}},
            fair={actor=function() return player end}}
        payments=0
        function storage.fair.place(name,p,direction)
            assert(direction==0 and surface.can_place_entity{name=name,position=p})
            payments=payments+1
            local e={valid=true,name=name,force=force,position=p,unit_number=20+payments}
            entities[name..":"..p.x..":"..p.y]=e
            return {name=name,position=p,unit_number=e.unit_number}
        end
        function external(name,p,unit)
            entities[name..":"..p.x..":"..p.y]={valid=true,name=name,force=force,
                position=p,unit_number=unit}
        end
        function remove(name,p) entities[name..":"..p.x..":"..p.y]=nil end
    ''')
    lua.execute(SOURCE)
    return lua


def begin(lua, *, points=(.5, 1.5), key="a" * 64):
    path = lua.table_from([{"x": x, "y": .5,
                            "existing": bool(lua.eval("storage.fair.actor().surface.find_entity")(
                                "pipe", lua.table_from({"x": x, "y": .5})))}
                           for x in points], recursive=True)
    return lua.eval("storage.campaign.connector_begin")(
        key, "source", "target", "pipe", "water", path)


def place(lua, index, x, *, key="a" * 64):
    return lua.eval("storage.fair.connector_place")(
        key, index, "pipe", lua.table_from({"x": x, "y": .5}), 0)


def test_each_paid_cell_has_native_unit_receipt_and_survives_additive_reload():
    lua = fixture()
    begin(lua)
    place(lua, 1, .5)
    place(lua, 2, 1.5)
    receipt = lua.eval("storage.campaign.connector_finish")("a" * 64)
    assert receipt.paid == 2 and receipt.external == 0 and receipt.owned
    assert lua.eval("payments") == 2
    lua.execute(SOURCE)
    observed = lua.eval("storage.campaign.observe_connector_ownership()")
    row = observed.routes["a" * 64]
    assert row.state == "complete" and row.owned and row.cell_count == 2
    page = lua.eval("storage.campaign.connector_page")("a" * 64, 1, 64)
    assert page.valid and page.cell_count == 2
    assert page.cells[1].unit_number == 21 and page.cells[1].paid == 1
    assert page.cells[2].unit_number == 22 and page.cells[2].paid == 1


def test_same_force_existing_cell_is_external_not_paid_ownership():
    lua = fixture()
    lua.execute('external("pipe",{x=.5,y=.5},99)')
    begin(lua)
    place(lua, 2, 1.5)
    receipt = lua.eval("storage.campaign.connector_finish")("a" * 64)
    assert receipt.paid == 1 and receipt.external == 1 and not receipt.owned
    page = lua.eval("storage.campaign.connector_page")("a" * 64, 1, 64)
    assert page.cells[1].unit_number == 99 and page.cells[1].paid == 0
    assert page.cells[2].unit_number == 21 and page.cells[2].paid == 1


def test_lost_reply_retains_pending_and_rejects_replay_or_adoption():
    lua = fixture()
    begin(lua)
    place(lua, 1, .5)
    with pytest.raises(LuaError, match="needs reconciliation"):
        begin(lua, key="b" * 64)
    with pytest.raises(LuaError, match="payment does not match"):
        place(lua, 1, .5)
    assert lua.eval("payments") == 1
    lua.execute('remove("pipe",{x=.5,y=.5})')
    row = lua.eval("storage.campaign.observe_connector_ownership().routes")["a" * 64]
    assert row.state == "fault" and not row.owned
    with pytest.raises(LuaError, match="changed"):
        place(lua, 2, 1.5)


def test_post_payment_exception_retains_pending_without_claiming_paid_cell():
    lua = fixture()
    begin(lua)
    lua.execute('''
        local original=storage.fair.place
        storage.fair.place=function(...) original(...);error("lost reply") end
    ''')
    with pytest.raises(LuaError, match="lost reply"):
        place(lua, 1, .5)
    row = lua.eval("storage.campaign.observe_connector_ownership().routes")["a" * 64]
    assert lua.eval("payments") == 1
    assert row.pending == 1 and row.paid == 0 and row.state == "building"
    with pytest.raises(LuaError, match="payment needs reconciliation"):
        lua.eval("storage.campaign.connector_finish")("a" * 64)


def test_preflight_and_owner_drift_reject_before_payment():
    lua = fixture()
    with pytest.raises(LuaError, match="repeats"):
        begin(lua, points=(.5, .5))
    assert lua.eval("storage.campaign.connector_ledger.active") is None
    begin(lua)
    lua.execute('storage.jev_session_id="other"')
    with pytest.raises(LuaError, match="owner changed"):
        place(lua, 1, .5)
    assert lua.eval("payments") == 0
