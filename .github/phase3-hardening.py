"""One-use follow-up integration for exact feature source; no native backend."""
from pathlib import Path
import subprocess
BASE = '766206de3eb16048811643af79faead2de2990d5'

def edit(path, before, after, count=1):
    p=Path(path);value=p.read_text();assert value.count(before)==count,(path,before,value.count(before))
    p.write_text(value.replace(before,after))

for path in ('src/jev_factorio/successor_controller.py','src/jev_factorio/lua/successors.lua',
             'src/jev_factorio/planning/successors.py','src/jev_factorio/supervisor.py',
             'src/jev_factorio/successors.py','tests/test_successors.py'):
    assert subprocess.check_output(['git','hash-object',path],text=True).strip()==subprocess.check_output(
        ['git','rev-parse',BASE+':'+path],text=True).strip(),path

edit('src/jev_factorio/successor_controller.py',
     "        self._successors_enabled = True\n",
     '''        # Validate opt-in migration before backend.enable_factory installs any
        # native extension. This read never rewrites the caller's checkpoint.
        if options.get('resume_controller'):
            from pathlib import Path
            path = Path(options.get('checkpoint') or '')
            data = json.loads(path.read_text(encoding='utf-8'))
            self.memory_type.load(path, data.get('session_id'), options['target'])
        self._successors_enabled = True
''')
edit('src/jev_factorio/successor_controller.py',
     "    def _pause_successor(self, source, reason):\n",
     '''    def _investment_step_allowed(self, plan, step, snapshot):
        if not super()._investment_step_allowed(plan, step, snapshot):
            return False
        marker = (plan.materials or {}).get(contract.MARKER, {})
        for source, project in self.memory.successor_projects.items():
            if project['status'] != 'active' or marker.get('source') == source and marker.get('anchor') == project['anchor']:
                continue
            try:
                site = site_sources(snapshot).get(source, {})
                if site.get('anchor') != project['anchor']:
                    return False
                required = dict(site['bill'])
                if project['source_unit']:
                    required['stone-furnace'] -= 1
                output = snapshot.factory.get('output_buffers', {}).get('sources', {}).get(source, {})
                for part in output.get('parts', {}):
                    name = 'wooden-chest' if part == 'chest' else 'burner-inserter'
                    required[name] -= 1
                route = snapshot.factory.get('input_routes', {}).get('sources', {}).get(source, {})
                for spec in route.get('steps', []):
                    if spec['part'] in route.get('parts', {}):
                        required[spec['name']] -= 1
                if any(v < 0 for v in required.values()):
                    return False
                # Only pieces already carried are protected. Coal and seed ore
                # are not locked away from predecessor/emergency maintenance.
                for item, cost in (step.costs or {}).items():
                    have = snapshot.inventory.get(item, 0)
                    if have - cost < min(have, required.get(item, 0)):
                        return False
            except (KeyError, TypeError, ValueError):
                return False
        return True

    def _pause_successor(self, source, reason):
''')
edit('src/jev_factorio/successor_controller.py',
     "or sum(p.get('status') != 'qualified' for p in memory.successor_projects.values()) > 1):",
     "or any(not isinstance(p, dict) for p in memory.successor_projects.values())\n                    or sum(p.get('status') != 'qualified' for p in memory.successor_projects.values()) > 1):")
edit('src/jev_factorio/planning/successors.py',
     "    def acquire(self, item, amount):\n",
     "    def _science_reserve(self):\n        return max(successors.SCIENCE_RESERVE, super()._science_reserve())\n\n    def acquire(self, item, amount):\n")

edit('src/jev_factorio/lua/successors.lua',
     'if not window or game.tick-window.last_tick>1800 then',
     'if not window or game.tick-window.last_tick>1800 or game.tick-window.last_progress_tick>1800 then')
edit('src/jev_factorio/lua/successors.lua',
     'baseline=count,last_produced=count,positive_samples=0}',
     'baseline=count,last_produced=count,positive_samples=0,last_progress_tick=game.tick,max_gap=0}')
edit('src/jev_factorio/lua/successors.lua',
     'if count>window.last_produced then window.positive_samples=window.positive_samples+1 end',
     'window.max_gap=math.max(window.max_gap,game.tick-window.last_tick)\n                    if count>window.last_produced then window.positive_samples=window.positive_samples+1;window.last_progress_tick=game.tick end')
edit('src/jev_factorio/lua/successors.lua',
     'positive_samples=window.positive_samples,produced=count-window.baseline,',
     'positive_samples=window.positive_samples,produced=count-window.baseline,\n                        last_progress_tick=window.last_progress_tick,max_observation_gap=window.max_gap,')
edit('src/jev_factorio/successors.py',
     "('first_tick', 'last_tick', 'positive_samples', 'produced', 'source_unit'))",
     "('first_tick', 'last_tick', 'positive_samples', 'produced', 'source_unit', 'last_progress_tick', 'max_observation_gap'))")
edit('src/jev_factorio/successors.py',
     "or proof['produced'] < 3 or proof['last_tick'] - proof['first_tick'] < 36000",
     "or proof['produced'] < 3 or proof['last_tick'] - proof['first_tick'] < 36000\n                or not proof['first_tick'] <= proof['last_progress_tick'] <= proof['last_tick']\n                or proof['last_tick'] - proof['last_progress_tick'] > 1800 or proof['max_observation_gap'] > 1800")
edit('tests/test_successors.py',
     "'positive_samples': 30, 'produced': 30, 'source_unit': 17,",
     "'positive_samples': 30, 'produced': 30, 'source_unit': 17,\n        'last_progress_tick': 36300, 'max_observation_gap': 1200,")

# Explicit supervisor treatment and repair state preservation, not automatic enablement.
edit('src/jev_factorio/supervisor.py',
     '    mining_outposts: bool = False\n',
     '    mining_outposts: bool = False\n    ore_side_successors: bool = False\n')
edit('src/jev_factorio/supervisor.py',
     '        if self.mining_outposts and not self.furnace_input_belts:',
     '''        if self.ore_side_successors and (not self.background_work or not self.furnace_input_belts or self.mining_outposts):
            raise ValueError('Successors require background-work input belts without mining outposts')
        if self.mining_outposts and not self.furnace_input_belts:''')
edit('src/jev_factorio/supervisor.py',
     "            configuration['mining_outposts'] = True\n",
     "            configuration['mining_outposts'] = True\n        if self.config.ore_side_successors:\n            configuration['ore_side_successors'] = True\n")
edit('src/jev_factorio/supervisor.py',
     'for name in ("background_work", "furnace_output_buffers", "furnace_input_belts", "mining_outposts"):',
     'for name in ("background_work", "furnace_output_buffers", "furnace_input_belts", "mining_outposts", "ore_side_successors"):')
edit('src/jev_factorio/supervisor.py',
     'mining_outposts={self.config.mining_outposts}\n',
     'mining_outposts={self.config.mining_outposts}, ore_side_successors={self.config.ore_side_successors}\n')
edit('src/jev_factorio/supervisor.py',
     '"input_routes_schema", "input_commitments", "outposts_schema", "outpost_commitments")',
     '"input_routes_schema", "input_commitments", "outposts_schema", "outpost_commitments",\n                              "successor_schema", "successor_projects", "successor_receipts")')
edit('src/jev_factorio/supervisor.py',
     'if previous.get("background_job") or previous.get("input_commitments") or previous.get("outpost_commitments"):',
     'if (previous.get("background_job") or previous.get("input_commitments") or previous.get("outpost_commitments")\n                    or previous.get("successor_projects")):')
edit('src/jev_factorio/supervisor.py',
     '    parser.add_argument("--mining-outposts", action="store_true")\n',
     '    parser.add_argument("--mining-outposts", action="store_true")\n    parser.add_argument("--ore-side-successors", action="store_true")\n')
print('Added preflight migration, protected construction inventory, sampled liveness and supervisor continuity')
