"""One-use branch integration. Removed before PR merge; no world access."""
from pathlib import Path
import subprocess

BASE = '1cb0c7afbe276becb79dd099bb40d713a64e31cb'

def edit(path, before, after, count=1):
    p = Path(path)
    value = p.read_text()
    assert value.count(before) == count, (path, before, value.count(before))
    p.write_text(value.replace(before, after))

# Pin every existing integration input; new feature modules are ordinary reviewed files.
for path in ['src/jev_factorio/controller.py', 'src/jev_factorio/factory_contract.py',
             'src/jev_factorio/main.py', 'src/jev_factorio/research_log.py',
             'src/jev_factorio/input_controller.py', 'src/jev_factorio/input_routes.py',
             'src/jev_factorio/output_buffers.py', 'src/jev_factorio/production_sites.py',
             'src/jev_factorio/planning/factory.py', 'src/jev_factorio/planning/demand.py',
             'src/jev_factorio/planning/output_buffers.py', 'src/jev_factorio/lua/input_routes.lua',
             'src/jev_factorio/lua/output_buffers.lua', 'src/jev_factorio/lua/production_sites.lua',
             'src/jev_factorio/lua/craft_jobs.lua', 'src/jev_factorio/capital_controller.py']:
    expected = subprocess.check_output(['git', 'rev-parse', BASE + ':' + path], text=True).strip()
    assert subprocess.check_output(['git', 'hash-object', path], text=True).strip() == expected, path

edit('src/jev_factorio/controller.py',
     "        if 'mining_outposts' in snapshot.factory and not getattr(self, '_mining_outposts_enabled', False):",
     "        if 'successors' in snapshot.factory and not getattr(self, '_successors_enabled', False):\n            raise ValueError('Existing successor runtime requires its explicit controller capability')\n        if 'mining_outposts' in snapshot.factory and not getattr(self, '_mining_outposts_enabled', False):")
edit('src/jev_factorio/controller.py',
     '            commit_capital(self, chosen, snapshot)\n',
     '            commit_capital(self, chosen, snapshot)\n            if getattr(self, "_commit_successor", None):\n                self._commit_successor(chosen, snapshot)\n')
edit('src/jev_factorio/capital_controller.py',
     '    original, blocker = loop._compile_candidates(snapshot)\n',
     "    original, blocker = loop._compile_candidates(snapshot)\n    if any('successor_project' in (p.materials or {}) for p in original):\n        return original, blocker  # An explicit successor proposal is not another capital kit.\n")
edit('src/jev_factorio/factory_contract.py',
     'from . import output_buffers, input_routes, production_sites, mining_outposts',
     'from . import output_buffers, input_routes, production_sites, mining_outposts, successors')
edit('src/jev_factorio/factory_contract.py',
     '    "player_bound", "machine", "machine_recipe", "machine_input", "machine_fuel",',
     '    "successor_route_available", "player_bound", "machine", "machine_recipe", "machine_input", "machine_fuel",')
edit('src/jev_factorio/factory_contract.py',
     '    if effect == "outpost_component":',
     '    if effect == "successor_route_available":\n        return action == "factory_wait" and parameters.get("role") in input_routes.sources(snapshot)\n    if effect == "outpost_component":')
edit('src/jev_factorio/factory_contract.py',
     '    validate_command(action, parameters)\n    if "mining_outposts" in snapshot.factory:',
     '''    validate_command(action, parameters)
    if "successors" in snapshot.factory:
        try:
            if not successors.permits(action, parameters, snapshot):
                return False
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
    elif parameters.get('role') in successors.ROLES or parameters.get('source') in successors.ROLES:
        return False
    if "mining_outposts" in snapshot.factory:''')
edit('src/jev_factorio/planning/successors.py',
     "            return self._wait('machine_output', site.get('item', role[7:]), 10, output['chest_role'],\n                              timeout=1800, identity='successor-route-survey:' + anchor)",
     "            return self._wait('successor_route_available', role=role, timeout=1800,\n                              identity='successor-route-survey:' + anchor)")
edit('src/jev_factorio/input_routes.py',
     'ORES = {"recipe:iron-plate": "iron-ore", "recipe:copper-plate": "copper-ore"}',
     'ORES = {"recipe:iron-plate": "iron-ore", "recipe:copper-plate": "copper-ore",\n        "growth:iron-plate": "iron-ore", "growth:copper-plate": "copper-ore"}')
edit('src/jev_factorio/input_routes.py',
     'or not isinstance(data.get("sources"), dict) or len(data["sources"]) > 2)',
     'or not isinstance(data.get("sources"), dict) or len(data["sources"]) > (4 if "successors" in snapshot.factory else 2))')
edit('src/jev_factorio/input_routes.py',
     '        if (source not in ORES or not isinstance(row, dict) or row.get("source") != source',
     '        if (source not in ORES or source.startswith("growth:") and "successors" not in snapshot.factory\n                or not isinstance(row, dict) or row.get("source") != source')
edit('src/jev_factorio/input_routes.py',
     '            if action == "factory_gather" and parameters.get("resource") == row["ore"]:',
     '            if source.startswith("recipe:") and action == "factory_gather" and parameters.get("resource") == row["ore"]:')
edit('src/jev_factorio/input_controller.py',
     'or not isinstance(memory.input_commitments, dict) or len(memory.input_commitments) > 2)',
     'or not isinstance(memory.input_commitments, dict)\n                    or len(memory.input_commitments) > (4 if hasattr(memory, "successor_schema") else 2))')
edit('src/jev_factorio/input_controller.py',
     '                if (source not in ORES or not isinstance(entry, dict)',
     '                if (source not in ORES or source.startswith("growth:") and not hasattr(memory, "successor_schema")\n                        or not isinstance(entry, dict)')
edit('src/jev_factorio/output_buffers.py',
     'or not isinstance(data.get("sources"), dict) or len(data["sources"]) > 3)',
     'or not isinstance(data.get("sources"), dict) or len(data["sources"]) > (5 if "successors" in snapshot.factory else 3))')
edit('src/jev_factorio/production_sites.py',
     "ROLES = {'recipe:iron-plate', 'recipe:copper-plate'}",
     "ROLES = {'recipe:iron-plate', 'recipe:copper-plate', 'growth:iron-plate', 'growth:copper-plate'}")
edit('src/jev_factorio/production_sites.py',
     "    for role, row in data['sources'].items():\n",
     "    for role, row in data['sources'].items():\n        if role.startswith('growth:') and 'successors' not in snapshot.factory:\n            raise ValueError('Successor production requires its explicit capability')\n")
edit('src/jev_factorio/planning/factory.py',
     '        for role, machine in sorted(self.entities.items()):\n',
     '''        for role, machine in sorted(self.entities.items()):
            if 'successors' in self.factory:
                from ..successors import private_output
                if private_output(role, self.snapshot):
                    continue  # Trial or preferred output needs the successor-aware planner.
''')
edit('src/jev_factorio/planning/demand.py',
     "        for role, machine in sorted(snapshot.factory.get('entities', {}).items()):\n",
     "        for role, machine in sorted(snapshot.factory.get('entities', {}).items()):\n            if 'successors' in snapshot.factory:\n                from ..successors import private_output\n                if private_output(role, snapshot):\n                    continue  # Uncollected qualification/trial stock is not general forecast supply.\n")
edit('src/jev_factorio/planning/output_buffers.py',
     '        for row in sources(self.snapshot).values():\n',
     '        for row in sources(self.snapshot).values():\n            if row.get("source", "").startswith("growth:"):\n                continue  # Explicit trial/preference policy owns successor collection.\n')

# Expand only a fixed role allowlist; the canonical recipe roles remain unchanged.
for name in ('input_routes.lua', 'production_sites.lua'):
    path = 'src/jev_factorio/lua/' + name
    old = ('local ores = {["recipe:iron-plate"] = "iron-ore", ["recipe:copper-plate"] = "copper-ore"}'
           if name == 'input_routes.lua' else
           'local ores={["recipe:iron-plate"]="iron-ore",["recipe:copper-plate"]="copper-ore"}')
    new = '''local ores={["recipe:iron-plate"]="iron-ore",["recipe:copper-plate"]="copper-ore",
    ["growth:iron-plate"]="iron-ore",["growth:copper-plate"]="copper-ore"}
local function producer_roles()
    local result={"recipe:iron-plate","recipe:copper-plate"}
    if campaign.successors_enabled then result[#result+1]="growth:iron-plate";result[#result+1]="growth:copper-plate" end
    return result
end'''
    edit(path, old, new)
    token = 'ipairs({"recipe:iron-plate","recipe:copper-plate"})'
    edit(path, token, 'ipairs(producer_roles())', count=2 if name == 'input_routes.lua' else 1)
edit('src/jev_factorio/lua/input_routes.lua',
     'assert(not recipe or "recipe:"..recipe.name==role, "Input recipe changed")',
     'assert(not recipe or recipe.name==string.sub(role,8), "Input recipe changed")')
edit('src/jev_factorio/lua/input_routes.lua',
     '    if campaign.observe_production_sites then result.production_sites=campaign.observe_production_sites() end\n',
     '    if campaign.observe_production_sites then result.production_sites=campaign.observe_production_sites() end\n    if campaign.observe_successors then result.successors=campaign.observe_successors(result) end\n')
edit('src/jev_factorio/lua/input_routes.lua',
     '    return previous_transfer(role,item,quantity,receipt,extracting)\n',
     '''    local before=campaign.successor_before_transfer and campaign.successor_before_transfer(role,item,quantity,extracting)
    local value=previous_transfer(role,item,quantity,receipt,extracting)
    if campaign.successor_after_transfer then campaign.successor_after_transfer(role,item,quantity,receipt,extracting,before) end
    return value
''')
edit('src/jev_factorio/lua/output_buffers.lua',
     'assert(not recipe or "recipe:" .. recipe.name == role, "Output-buffer source recipe changed")',
     'assert(not recipe or recipe.name == string.sub(role,8), "Output-buffer source recipe changed")')
edit('src/jev_factorio/lua/output_buffers.lua',
     '    for _, item in ipairs(supported) do\n        local role = "recipe:" .. item\n',
     '''    local roles={}
    for _,item in ipairs(supported) do roles[#roles+1]="recipe:"..item end
    if campaign.successors_enabled then roles[#roles+1]="growth:iron-plate";roles[#roles+1]="growth:copper-plate" end
    for _, role in ipairs(roles) do
        local item=string.sub(role,8)
''')
edit('src/jev_factorio/lua/production_sites.lua',
     '        elseif campaign.entities[role] then\n',
     '''        elseif string.sub(role,1,7)=="growth:" and not campaign.entities["recipe:"..string.sub(role,8)] then
            sites.offers[role]=nil;reason="predecessor_missing"
        elseif campaign.entities[role] then
''')
edit('src/jev_factorio/lua/production_sites.lua',
     '    return site,player\nend\ncampaign.prepare_production_site',
     '    if campaign.successor_admit then campaign.successor_admit(site,false) end\n    return site,player\nend\ncampaign.prepare_production_site')
edit('src/jev_factorio/lua/production_sites.lua',
     '    local site=chosen(role,name,anchor)\n',
     '    local site=chosen(role,name,anchor)\n    if campaign.successor_prepare then campaign.successor_prepare(site) end\n')
edit('src/jev_factorio/lua/production_sites.lua',
     '    local site,player=chosen(role,name,anchor)\n',
     '    local site,player=chosen(role,name,anchor)\n    if campaign.successor_admit then campaign.successor_admit(site,true) end\n')
edit('src/jev_factorio/lua/production_sites.lua',
     '    sites.owned[role]=site;sites.offers[role]=nil\n',
     '    sites.owned[role]=site;sites.offers[role]=nil\n    if campaign.successor_bound then campaign.successor_bound(site) end\n')
edit('src/jev_factorio/lua/craft_jobs.lua',
     '    rcon.print("Native craft accepted; output remains unverified")\n',
     '    if campaign.successor_craft_paid then campaign.successor_craft_paid(job,before) end\n    rcon.print("Native craft accepted; output remains unverified")\n')
edit('src/jev_factorio/lua/successors.lua',
     'and route.layout==claim.layout and claim.quantity>=3 then',
     'and route.layout==claim.layout and claim.quantity>=3 and not m.use then')

edit('src/jev_factorio/main.py',
     '    p.add_argument("--mining-outposts", action="store_true",',
     '    p.add_argument("--ore-side-successors", action="store_true",\n                   help="Opt-in additive ore-side producers; requires background work and input belts")\n    p.add_argument("--mining-outposts", action="store_true",')
edit('src/jev_factorio/main.py',
     '    if args.mining_outposts and (not args.furnace_input_belts',
     '''    if args.ore_side_successors and (not args.furnace_input_belts or not args.background_work
                                     or args.mining_outposts or args.target != 'rocket_launch'
                                     or not args.resume or not args.resume_controller):
        p.error('--ore-side-successors requires background-work input belts, rocket goal, existing resumed campaign, and no mining outposts')
    if args.mining_outposts and (not args.furnace_input_belts''')
edit('src/jev_factorio/main.py',
     'furnace_input_belts=args.furnace_input_belts, mining_outposts=args.mining_outposts,',
     'furnace_input_belts=args.furnace_input_belts, mining_outposts=args.mining_outposts,\n            ore_side_successors=args.ore_side_successors,')
edit('src/jev_factorio/main.py',
     '            if args.mining_outposts:\n',
     '            if args.ore_side_successors:\n                from .successor_controller import successor_loop_type\n                loop_type = successor_loop_type(loop_type)\n            if args.mining_outposts:\n')
edit('src/jev_factorio/research_log.py',
     '    mining_outposts: bool = False\n',
     '    mining_outposts: bool = False\n    ore_side_successors: bool = False\n')
print('Integrated explicit successor roles without changing canonical identities or mutation commands')
