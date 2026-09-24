from pathlib import Path
import subprocess

expected = {'src/jev_factorio/controller.py': '86b11604009569ed54ab8c8fa38e7d5f053aa913',
            'src/jev_factorio/lua/factory.lua': '13229477f794430d1883e504abff658b4e82cbcf'}
for name, digest in expected.items():
    assert subprocess.check_output(['git','hash-object',name],text=True).strip()==digest,name

def edit(name, old, new):
    path=Path(name);text=path.read_text();assert text.count(old)==1,name
    path.write_text(text.replace(old,new,1))

edit('src/jev_factorio/controller.py','        record.update(self._record_extras())\n',
'''        record.update(self._record_extras())
        record["acceptance_configuration"] = {
            "factory_scheduling": getattr(self, "factory_scheduling", "serial"),
            **{name: record.get(name) is True for name in (
                "background_work", "furnace_output_buffers", "furnace_input_belts",
                "mining_outposts", "ore_side_successors")},
        }
''')
edit('src/jev_factorio/controller.py','''    def _model_facts(self, snapshot: GameSnapshot) -> dict:
        return snapshot.for_jev()
''','''    def _model_facts(self, snapshot: GameSnapshot) -> dict:
        facts = snapshot.for_jev()
        # Diagnostic-only additions must not grow/change model prompts.
        facts.get("factory", {}).pop("acceptance_runtime", None)
        facts.get("factory", {}).pop("consumed", None)
        return facts
''')
edit('src/jev_factorio/lua/factory.lua','''    local produced = {}
    for name in pairs(prototypes.item) do
        local amount = statistics.get_input_count(name)
        if amount > 0 then produced[name] = amount end
    end
''','''    local produced = {}
    local consumed = statistics.get_output_count and {} or nil
    for name in pairs(prototypes.item) do
        local amount = statistics.get_input_count(name)
        if amount > 0 then produced[name] = amount end
        if consumed then
            local used = statistics.get_output_count(name)
            if used > 0 then consumed[name] = used end
        end
    end
''')
edit('src/jev_factorio/lua/factory.lua','''        produced = produced,
''','''        produced = produced,
        consumed = consumed,
        -- Read existing properties in this observation; no extra RPC or mutation.
        acceptance_runtime = {schema=1, speed=game.speed, tick_paused=game.tick_paused,
            session_id=storage.jev_session_id, actor_unit=agent.unit_number,
            player_index=player and player.index, surface_index=agent.surface.index,
            force_index=force.index, mods=script and script.active_mods or nil},
''')
edit('src/jev_factorio/acceptance_capture.py',"'ore_side_successors buffer_evidence", "'process_id factory_scheduling goal history ore_side_successors buffer_evidence")
# A hash of the actual bytes is sufficient; formatting is deliberately irrelevant.
edit('src/jev_factorio/dev_preflight.py','''    if stable_read(checkpoint_path) != canonical(checkpoint):
        # Hash bytes, not formatting: most existing checkpoints are pretty JSON.
        if sha256(stable_read(checkpoint_path)) != checkpoint_hash:
            raise ValueError('Checkpoint changed during native probe')
''','''    if sha256(stable_read(checkpoint_path)) != checkpoint_hash:
        raise ValueError('Checkpoint changed during native probe')
''')
