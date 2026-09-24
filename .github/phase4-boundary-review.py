from pathlib import Path

def edit(name, old, new):
    path=Path(name);text=path.read_text();assert text.count(old)==1,(name,old[:90])
    path.write_text(text.replace(old,new,1))

edit('src/jev_factorio/acceptance_capture.py',"'production_vm_uuid', 'goal', 'configuration'}",
     "'production_vm_uuid', 'goal', 'configuration', 'expected_source_sha256'}")
edit('src/jev_factorio/acceptance_capture.py',
     "(('expected_commit', 40), ('initial_save_sha256', 64), ('initial_checkpoint_sha256', 64)):",
     "(('expected_commit', 40), ('expected_source_sha256', 64), ('initial_save_sha256', 64), ('initial_checkpoint_sha256', 64)):")
edit('src/jev_factorio/native_acceptance.py',"    resolved_models = set()",
     "    resolved_models, process_ids, execution_ids = set(), set(), set()")
edit('src/jev_factorio/native_acceptance.py',
     "        reject(row.get('run_id') != trial['trial_id'], 'trial_run_identity_mismatch')",
'''        reject(row.get('run_id') != trial['trial_id'], 'trial_run_identity_mismatch')
        for key, observed in (('process_id', process_ids), ('execution_id', execution_ids)):
            value = row.get(key)
            if not isinstance(value, str) or not 0 < len(value) <= 128:
                issues.append('invocation_identity_missing')
            else:
                observed.add(value)''')
edit('src/jev_factorio/native_acceptance.py',
     "        reject(revision.get('commit') != trial['expected_commit'] or revision.get('dirty') is True, 'source_revision_mismatch')",
'''        reject(revision.get('commit') != trial['expected_commit']
               or revision.get('source_sha256') != trial['expected_source_sha256']
               or revision.get('dirty') is True, 'source_revision_mismatch')''')
edit('src/jev_factorio/native_acceptance.py',
     "    reject(len(resolved_models) > 1, 'resolved_model_drift')",
'''    reject(len(resolved_models) > 1, 'resolved_model_drift')
    reject(len(process_ids) != 1 or len(execution_ids) != 1, 'interrupted_or_mixed_invocation')''')
# Both baseline and treatment consistency check the fingerprint, not commit alone.
p=Path('src/jev_factorio/native_acceptance.py');text=p.read_text()
old="for field in ('expected_commit', 'expected_policy', 'expected_model', 'configuration', 'goal'):"
assert text.count(old)==2
p.write_text(text.replace(old,"for field in ('expected_commit', 'expected_source_sha256', 'expected_policy', 'expected_model', 'configuration', 'goal'):"))
edit('tests/test_native_acceptance.py',
     "'pair_id': pair_id, 'arm': arm, 'expected_commit': COMMIT, 'expected_policy': 'hybrid',",
     "'pair_id': pair_id, 'arm': arm, 'expected_commit': COMMIT, 'expected_source_sha256': 'b' * 64, 'expected_policy': 'hybrid',")
edit('tests/test_native_acceptance.py',
     "'resolved_model': None, 'run_id': trial_id, 'segment_id': 'one', 'execution_id': trial_id,",
     "'resolved_model': None, 'run_id': trial_id, 'segment_id': 'one', 'execution_id': trial_id, 'process_id': trial_id,")
edit('tests/test_acceptance_review.py',
     "'production_vm_uuid':'production','expected_commit':'baseline' if arm=='baseline' else 'treatment',",
     "'production_vm_uuid':'production','expected_source_sha256':'source','expected_commit':'baseline' if arm=='baseline' else 'treatment',")
edit('docs/NATIVE_ACCEPTANCE.md',
     '  "expected_commit": "REPLACE_WITH_EXACT_40_HEX_COMMIT",',
     '  "expected_commit": "REPLACE_WITH_EXACT_40_HEX_COMMIT",\n  "expected_source_sha256": "REPLACE_WITH_64_HEX_SUPERVISOR_SOURCE_FINGERPRINT",')
edit('docs/NATIVE_ACCEPTANCE.md',
     'Repeated trial IDs, ambiguous pairs,', 'Repeated trial IDs, ambiguous pairs,') if False else None
edit('docs/NATIVE_ACCEPTANCE.md',
     'checkpoint/session/tick boundaries, exact code/policy/model/configuration, observed',
     'checkpoint/session/tick boundaries, exact commit and source fingerprint, policy/model/configuration, observed')
edit('docs/NATIVE_ACCEPTANCE.md',
     'incomplete arms, treatment/baseline drift, stale preflight, insufficient horizons',
     'incomplete arms, treatment/baseline drift, missing or changed process/execution identity, stale preflight, insufficient horizons')
p=Path('docs/NATIVE_ACCEPTANCE.md')
p.write_text(p.read_text()+'''\n## Source identity and uninterrupted-trial limits\n\nCopy the complete `commit` and `source_sha256` pair from the existing supervisor's\nsource-revision evidence into the predeclared trial. A commit alone cannot detect\na dirty or otherwise different working tree. `source_sha256` is this repository's\nversioned source fingerprint, not a SHA256 of the commit string, archive, or save.\nA missing or changed fingerprint makes the trial ineligible. This still relies on\ntrusted collection; it does not make unsigned gameplay JSON externally authentic.\n\nPerformance pairs and the uninterrupted soak each require one nonempty controller\n`process_id` and one `execution_id`. A restart within a capture fails that gate\neven if cumulative counter values happen to exceed their previous values after\nreattachment. Restart/fault trials are separate recovery evidence, not silently\npooled into uninterrupted performance measurements.\n''')
