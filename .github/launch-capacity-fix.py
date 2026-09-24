from pathlib import Path

def edit(name, old, new):
    p=Path(name);text=p.read_text();assert text.count(old)==1,(name,old)
    p.write_text(text.replace(old,new,1))

edit('src/jev_factorio/lua/launch_readiness.lua',
 '    return pad.can_insert{name="space-science-pack",count=1000}',
 '''    local inventory=pad.get_inventory(defines.inventory.cargo_landing_pad_main)
    -- can_insert only proves that *some* of a stack fits. These normal,
    -- non-durable items use the basic inventory's full insertable count.
    return inventory and inventory.get_insertable_count("space-science-pack")>=1000 or false''')
edit('src/jev_factorio/lua/launch_readiness.lua',
 '    assert(player.get_main_inventory().can_insert{name="raw-fish",count=5}, "No room for native fish yield")',
 '    assert(player.get_main_inventory().get_insertable_count("raw-fish")>=5, "No room for native fish yield")')
edit('tests/fixtures/launch_runtime.lua',
 '    value.can_insert=function(q) return (stock[q.name] or 0)+q.count<=value.limit end',
 '''    value.can_insert=function(q) return q.count>0 and (stock[q.name] or 0)<value.limit end
    value.get_insertable_count=function(name) return math.max(0,value.limit-(stock[name] or 0)) end''')
edit('tests/fixtures/launch_runtime.lua','defines={inventory={rocket_silo_rocket=99}',
 'defines={inventory={rocket_silo_rocket=99,cargo_landing_pad_main=100}')
p=Path('tests/fixtures/launch_runtime.lua');text=p.read_text()
old='can_insert=function(q)return not pad_full end'
assert text.count(old)==2
text=text.replace(old,'''can_insert=function(q)return not pad_full end,
            get_inventory=function(index) assert(index==100);return {
                get_insertable_count=function(item) assert(item=="space-science-pack")
                    return pad_full and 0 or (pad_room or 1000) end} end''')
p.write_text(text)
p=Path('tests/test_launch_readiness.py');p.write_text(p.read_text()+'''

@pytest.mark.parametrize('room', [0, 1, 999])
def test_partial_pad_capacity_does_not_authorize_loading_or_launch(room):
    lua=lua_case('')
    lua.execute(f'add_pad();pad_room={room};main.stock["satellite"]=1')
    lua.execute(\'''local row=observe()
        assert(row.pad.accepts.satellite==false)
        assert(not pcall(storage.campaign.load_launch_payload,{
            role="recipe:rocket-part",silo_unit=30,rocket_unit=31,item="satellite",receipt="short-pad"}))
        assert(not storage.launch_readiness.attempts.load and main.stock.satellite==1 and cargo.is_empty())
        cargo.stock.satellite=1
        assert(not pcall(storage.campaign.launch,"recipe:rocket-part"))
        assert(not storage.launch_readiness.attempts.launch and launches==0)
    \''')


@pytest.mark.parametrize('room', [0, 1, 4])
def test_partial_main_inventory_capacity_does_not_start_fish_mining(room):
    lua=lua_case('');lua.execute(f'main.limit={room}')
    lua.execute(\'''local row=observe()
        assert(not pcall(storage.campaign.begin_launch_fish,{target=row.fish.id,receipt="short-fish"}))
        assert(not storage.launch_readiness.attempts.fish and mines==0 and fish.valid)
    \''')


def test_exact_five_item_fish_capacity_is_enough():
    lua=lua_case('');lua.execute(\'''main.limit=5;local row=observe()
        storage.campaign.begin_launch_fish{target=row.fish.id,receipt="five-fish"}
        harvest_event()
        assert(main.get_item_count("raw-fish")==5 and storage.launch_readiness.receipts["five-fish"])
    \''')
''')
p=Path('docs/LAUNCH_READINESS.md');p.write_text(p.read_text()+'''

### Partial-capacity API semantics

In the pinned runtime, `can_insert` means **at least some** of a requested stack
fits; it is not proof that all five harvested fish or 1,000 satellite products
fit. Full-batch admission uses `get_insertable_count` on the character's main
inventory and the pad's `cargo_landing_pad_main` inventory. Its documented basic-
item/basic-inventory scope matches these normal, non-durable items; the native
launch remains final authority. Tests include one free slot/unit and 999-space-
science capacity, not just entirely full/empty destinations. The one-unit cargo
transfer still checks its actual inserted count and preserves an ambiguous intent.

- [Pinned inventory insertion semantics](https://lua-api.factorio.com/2.0.77/classes/LuaInventory.html)
''')
