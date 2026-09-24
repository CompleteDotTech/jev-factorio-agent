import json

import pytest

from jev_factorio.backends.fair_actions import FairActions


def test_water_route_preserves_empty_petroleum_port_and_its_clearance():
    lua = pytest.importorskip("lupa.lua54").LuaRuntime()
    lua.execute("""
        force={}
        defines={build_check_type={manual=1}}
        plant={name='chemical-plant',position={x=12.5,y=51.5},fluidbox={{},{}}}
        plant.fluidbox[1]=false; plant.fluidbox[2]=false
        plant.fluidbox.get_filter=function(i) return {name=i==1 and 'water' or 'petroleum-gas'} end
        plant.fluidbox.get_pipe_connections=function(i)
            return {{connection_type='normal',target_position={x=i==1 and 11.5 or 13.5,y=49.5}}}
        end
        gas={name='pipe',position={x=20.5,y=49.5},fluidbox={{name='petroleum-gas'}}}
        gas.fluidbox.get_filter=function(_) return nil end
        gas.fluidbox.get_pipe_connections=function(_)
            return {{connection_type='normal',target_position={x=19.5,y=49.5}},
                    {connection_type='normal',target_position={x=21.5,y=49.5}},
                    {connection_type='normal',target_position={x=20.5,y=48.5}},
                    {connection_type='normal',target_position={x=20.5,y=50.5}}}
        end
        gas.force=force
        surface={find_entities_filtered=function(filter)
                    assert(filter.area); return {plant,gas} end,
                 find_entity=function(name,p)
                    if p.x==20.5 and p.y==49.5 then return gas end end,
                 can_place_entity=function(_) return true end}
        player={force=force,surface=surface}
        storage={fair={actor=function() return player end}}
        helpers={table_to_json=function(value) captured=value;return '' end}
        rcon={print=function(_) end}
    """)
    fair = object.__new__(FairActions)
    def command(script):
        lua.execute(script)
        return json.dumps({key: [dict(p) for p in lua.globals().captured[key].values()]
                           for key in ("buildable", "existing")})
    fair.command = command
    buildable, existing = fair._connection_cells("pipe", "water", [(10, 22, 48, 51)])
    assert not existing
    assert (11.5, 49.5) in buildable  # Correct native water inlet.
    assert (10.5, 49.5) in buildable  # A safe route into that inlet remains.
    for cell in ((13.5, 49.5), (12.5, 49.5), (14.5, 49.5), (13.5, 48.5), (13.5, 50.5)):
        assert cell not in buildable  # Reserve gas inlet even while empty.
    for cell in ((20.5, 49.5), (19.5, 49.5), (21.5, 49.5), (20.5, 48.5), (20.5, 50.5)):
        assert cell not in buildable  # Never merge into an existing gas segment.
    assert (18.5, 49.5) in buildable  # No second halo around occupied pipes.
