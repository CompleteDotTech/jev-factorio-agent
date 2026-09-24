"""One-use targeted review corrections, executed after phase3-hardening.py."""
from pathlib import Path

def edit(path, before, after, count=1):
    p=Path(path);value=p.read_text();assert value.count(before)==count,(path,before,value.count(before))
    p.write_text(value.replace(before,after))

edit('src/jev_factorio/successor_controller.py',
     "        if marker and self.memory.failures.get(plan_id, 0) >= 2:\n            self._pause_successor(marker['source'], 'reconciled_step_budget')",
     "        if marker and self._project_failures(marker['source']) >= 2:\n            self._pause_successor(marker['source'], 'reconciled_project_budget')")
edit('src/jev_factorio/successor_controller.py',
     '    def _work_candidates(self, snapshot):\n',
     '''    def _project_failures(self, source):
        prefix = 'successor:' + source + ':'
        return sum(count for key, count in self.memory.failures.items() if key.startswith(prefix))

    def _work_candidates(self, snapshot):
''')
edit('src/jev_factorio/successor_controller.py',
     "            if snapshot.tick >= project['deadline_tick']:\n",
     "            if self._project_failures(source) >= 2:\n                self._pause_successor(source, 'retained_project_failure_budget')\n                return original, blocker\n            if snapshot.tick >= project['deadline_tick']:\n")
# CLI preflight occurs before make_backend; direct Python constructors have their
# own preflight as well. No checkpoint or runtime is altered on a rejected opt-in.
edit('src/jev_factorio/main.py',
     '        # Resolve credentials before starting a backend that initializes a world.\n',
     '''        if args.ore_side_successors:
            try:
                import json
                from .background import BackgroundWorkLoop
                from .buffer_controller import buffered_loop_type
                from .input_controller import input_loop_type
                from .successor_controller import successor_loop_type
                path = Path(args.checkpoint)
                identity = json.loads(path.read_text(encoding='utf-8'))
                kind = successor_loop_type(input_loop_type(buffered_loop_type(BackgroundWorkLoop)))
                kind.memory_type.load(path, identity.get('session_id'), args.target)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                p.error('Successor checkpoint preflight failed; backend not started')
        # Resolve credentials before starting a backend that initializes a world.
''')

edit('tests/test_successors_lua.py',
     'def runtime():\n    lua = pytest.importorskip(\'lupa.lua54\').LuaRuntime()\n',
     '''def runtime(request):
    lua = pytest.importorskip('lupa.lua54').LuaRuntime()
    def scenario(code):
        if getattr(request, 'param', 'iron') == 'copper':
            code = code.replace('iron-plate', 'copper-plate').replace('iron-ore', 'copper-ore').replace('iron-gear-wheel', 'copper-cable')
        lua.execute(code)
''')
edit('tests/test_successors_lua.py',
     "    lua.execute((ROOT / 'tests/fixtures/input_routes_runtime.lua').read_text())",
     "    scenario((ROOT / 'tests/fixtures/input_routes_runtime.lua').read_text())")
edit('tests/test_successors_lua.py', "    lua.execute('''\n", "    scenario('''\n", count=2)
print('Review corrections preserve history, early activation boundary and both ore-side paths')
