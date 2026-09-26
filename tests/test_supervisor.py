import json
from pathlib import Path

import pytest

from jev_factorio.supervisor import Supervisor, SupervisorConfig, atomic_json


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeProcess:
    pid = 99999999

    def __init__(self, code=None):
        self.returncode = code
        self.reaped = False

    def poll(self):
        return self.returncode

    def wait(self):
        self.reaped = True
        self.returncode = -9
        return self.returncode


@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    checkpoint = tmp_path / "checkpoint.json"
    atomic_json(checkpoint, {"session_id": "fresh", "target": "rocket_launch",
                             "status": "running", "pending": None})
    clock = FakeClock()
    config = SupervisorConfig(
        state_dir=tmp_path / "watchdog", checkpoint=checkpoint,
        session_id="fresh", started_at=1000, repair_command=["repair"],
        cwd=tmp_path, duration_hours=0.01, poll_seconds=1, hang_seconds=3,
        backoff_seconds=1,
    )
    config.state_dir.mkdir()
    instance = Supervisor(config, clock=clock, sleep=clock.sleep,
                          popen=lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(instance, "kill_group", lambda pid: None)
    monkeypatch.setattr(instance, "lock_path", lambda: tmp_path / ".supervisor.lock")
    monkeypatch.setattr(instance, "source_identity", lambda: ("head", "diff"))
    instance.initialize()
    return instance


def test_cutoff_survives_restart_and_cannot_extend(supervisor):
    original = supervisor.state["cutoff"]
    supervisor.clock.sleep(10)
    supervisor.initialize()
    assert supervisor.state["cutoff"] == original
    supervisor.config.started_at += 1
    with pytest.raises(ValueError, match="cannot be changed"):
        supervisor.initialize()


def test_resume_command_preserves_world_and_controller(supervisor):
    command = supervisor.gameplay_command()
    assert "--resume" in command and "--resume-controller" in command
    assert command[command.index("--policy") + 1] == "hybrid"
    assert command[command.index("--target") + 1] == "rocket_launch"
    assert "--run-dir" not in command
    assert command[command.index("--factory-scheduling") + 1] == "serial"
    assert "--background-work" not in command
    assert "--furnace-output-buffers" not in command
    assert "--furnace-input-belts" not in command


def test_production_extensions_forwarded_on_every_launch(supervisor):
    supervisor.config.factory_scheduling = "ready-work"
    supervisor.config.background_work = True
    supervisor.config.furnace_output_buffers = True
    supervisor.config.furnace_input_belts = True
    for _ in range(2):
        command = supervisor.gameplay_command()
        assert command[command.index("--factory-scheduling") + 1] == "ready-work"
        for flag in ("--background-work", "--furnace-output-buffers", "--furnace-input-belts"):
            assert flag in command
        assert "--resume" in command and "--resume-controller" in command


@pytest.mark.parametrize("extension", [
    "background_work", "furnace_output_buffers", "furnace_input_belts",
])
def test_production_extensions_require_ready_work(supervisor, extension):
    setattr(supervisor.config, extension, True)
    with pytest.raises(ValueError, match="ready-work"):
        supervisor.gameplay_command()


def test_input_belts_require_output_buffers(supervisor):
    supervisor.config.factory_scheduling = "ready-work"
    supervisor.config.furnace_input_belts = True
    with pytest.raises(ValueError, match="output buffers"):
        supervisor.gameplay_command()


def test_restart_cannot_silently_change_production_configuration(supervisor):
    supervisor.config.factory_scheduling = "ready-work"
    with pytest.raises(ValueError, match="configuration cannot be changed"):
        supervisor.initialize()


def test_research_evidence_is_exclusive_per_gameplay_invocation(supervisor, tmp_path):
    supervisor.config.research_dir = tmp_path / "research"
    commands = [supervisor.gameplay_command(), supervisor.gameplay_command()]
    paths = [Path(command[command.index("--run-dir") + 1]) for command in commands]
    assert paths[0] != paths[1]
    assert all(path.parent == supervisor.config.research_dir for path in paths)
    assert not any(path.exists() for path in paths)
    assert all("--resume" in command and "--resume-controller" in command for command in commands)


def test_repair_prompt_preserves_fairness_on_every_attempt(supervisor, tmp_path):
    for attempt in (1, 2):
        supervisor.state["attempt"] = attempt
        prompt = supervisor.repair_prompt("execution_failed", tmp_path / "result.json")
        for requirement in (
            "actual walking",
            "normal mining",
            "standard interaction reach",
            "1x game speed",
            "Never restore teleportation",
            "fast movement/mining bypasses",
            "remote interaction\nbeyond standard reach",
            "scripted harvest or inventory grants",
            "elapsed-time-only\nsimulation of walking/mining",
            "game/player speed changes",
            "focused regression tests for affected fairness behavior",
            "independent exact-head review to inspect it",
            "distinguishing mock tests from native proof",
            "Do not report repaired or permit resume with a fairness regression",
            "report blocked with the missing evidence",
        ):
            assert requirement in prompt


def test_repair_prompt_requires_completed_fixes_and_preserves_resume_identity(supervisor, tmp_path):
    prompt = supervisor.repair_prompt("execution_failed", tmp_path / "result.json")
    for requirement in (
        "Complete future bug fixes inside this repair loop",
        "unpublished patch is not a completed code repair",
        "Complete fix, tests, independent review, publication/merge, and synchronization",
        "the supervisor alone resumes gameplay",
        "within the original cutoff",
        "original session and pending identity",
        f"Session: {supervisor.config.session_id}",
        f"Absolute wallclock cutoff (Unix seconds): {supervisor.state['cutoff']}",
        "leave any existing pending action unchanged",
        "preserve its active_plan, step_index, and reservations exactly",
    ):
        assert requirement in prompt


@pytest.mark.parametrize("status", ["blocked", "uncertain", "completed"])
def test_terminal_checkpoint_does_not_launch(supervisor, status):
    checkpoint = supervisor.checkpoint()
    checkpoint["status"] = status
    atomic_json(supervisor.config.checkpoint, checkpoint)
    supervisor.popen = lambda *args, **kwargs: pytest.fail("should not launch")
    assert supervisor.watch_game() == status


def test_changed_code_with_pending_action_starts_repair_without_gameplay(supervisor, monkeypatch):
    before = {"commit": "a" * 40, "source_sha256": "1" * 64}
    after = {"commit": "b" * 40, "source_sha256": "2" * 64}
    checkpoint = supervisor.checkpoint()
    checkpoint.update(
        pending={"dispatch": "prepared", "action": "factory_insert", "started_tick": 12},
        active_plan={"id": "factory:factory_insert:recipe:copper-plate"},
        step_index=0,
        reservations={"factory:factory_insert:recipe:copper-plate": {"copper-ore": 3}},
    )
    original = json.dumps(checkpoint, sort_keys=True)
    atomic_json(supervisor.config.checkpoint, checkpoint)
    supervisor.save(code_revision=before)
    monkeypatch.setattr(supervisor, "snapshot_revision", lambda: after)
    supervisor.popen = lambda *args, **kwargs: pytest.fail("pending code transition launched gameplay")

    assert supervisor.watch_game() == "checkpoint_reconciliation"
    assert supervisor.state["repair_required"] is True
    assert supervisor.state["incident"]["checkpoint"] == checkpoint
    assert supervisor.state["code_revision"] == before
    assert json.dumps(supervisor.checkpoint(), sort_keys=True) == original


def test_manual_changed_code_with_pending_action_is_rejected_before_audit(supervisor, monkeypatch):
    before = {"commit": "a" * 40, "source_sha256": "1" * 64}
    after = {"commit": "b" * 40, "source_sha256": "2" * 64}
    checkpoint = supervisor.checkpoint()
    checkpoint.update(
        pending={"dispatch": "prepared", "action": "factory_insert", "started_tick": 12},
        active_plan={"id": "factory:factory_insert:recipe:copper-plate"},
        step_index=0,
        reservations={"factory:factory_insert:recipe:copper-plate": {"copper-ore": 3}},
    )
    atomic_json(supervisor.config.checkpoint, checkpoint)
    supervisor.save(code_revision=before)
    state = json.dumps(supervisor.state, sort_keys=True)
    monkeypatch.setattr(supervisor, "snapshot_revision", lambda **kwargs: after)

    with pytest.raises(ValueError, match="pending action requires reconciliation"):
        supervisor.record_manual_intervention(
            {"actor": "operator", "reason": "code_change", "evidence": ["reviewed"]}
        )

    assert json.dumps(supervisor.state, sort_keys=True) == state
    assert supervisor.checkpoint() == checkpoint


def test_hang_detected_and_process_reaped(supervisor):
    assert supervisor.watch_game() == "checkpoint_heartbeat_timeout"
    process = supervisor.process
    supervisor.stop_process()
    assert process.reaped
    assert supervisor.state["process"] is None


def test_successful_exit_still_requires_completed_checkpoint(supervisor):
    supervisor.popen = lambda *args, **kwargs: FakeProcess(0)
    assert supervisor.watch_game() == "process_exit: 0"
    supervisor.stop_process()


def test_cutoff_stops_hung_process(supervisor):
    supervisor.config.hang_seconds = 1000
    assert supervisor.watch_game() == "cutoff"
    process = supervisor.process
    supervisor.stop_process()
    assert process.reaped
    assert supervisor.clock() == supervisor.state["cutoff"]


def operational_result(supervisor, tmp_path):
    result = tmp_path / "result.json"
    atomic_json(result, {
        "status": "repaired", "kind": "operational", "session_id": "fresh",
        "checkpoint": str(supervisor.config.checkpoint.resolve()),
        "operational_verified": True, "evidence": ["observed receipts retained"],
    })
    return result


def test_operational_result_requires_unchanged_source(supervisor, tmp_path, monkeypatch):
    result = operational_result(supervisor, tmp_path)
    monkeypatch.setattr(supervisor, "source_identity", lambda: ("head", "diff"))
    assert supervisor.validate_repair(result, supervisor.checkpoint(), ("head", "diff"))
    assert not supervisor.validate_repair(result, supervisor.checkpoint(), ("other", "diff"))


def test_repair_cannot_introduce_legacy_failure_attribution(supervisor, tmp_path, monkeypatch):
    from jev_factorio.planning.connection_identity import PREFIX, connection_key
    result = operational_result(supervisor, tmp_path)
    monkeypatch.setattr(supervisor, 'source_identity', lambda: ('head', 'diff'))
    previous = supervisor.checkpoint()
    current = dict(previous, connection_failure_attribution={})
    atomic_json(supervisor.config.checkpoint, current)
    assert supervisor.validate_repair(result, previous, ('head', 'diff'))
    key = PREFIX + connection_key(dict(source='pump', target='refinery', kind='pipe', fluid='crude-oil'))
    previous['failures'] = {PREFIX: 2}
    current['failures'] = {PREFIX: 2, key: 2}
    current['connection_failure_attribution'] = {
        PREFIX: dict(count=2, allocations={key: 2}, evidence='plausible but unapproved attribution')}
    atomic_json(supervisor.config.checkpoint, current)
    assert not supervisor.validate_repair(result, previous, ('head', 'diff'))


def test_pending_cannot_be_cleared_by_repair_ack(supervisor, tmp_path, monkeypatch):
    result = operational_result(supervisor, tmp_path)
    monkeypatch.setattr(supervisor, "source_identity", lambda: ("head", "diff"))
    previous = {**supervisor.checkpoint(), "pending": {"dispatch": "ambiguous"}}
    assert not supervisor.validate_repair(result, previous, ("head", "diff"))


def test_repair_ack_alone_is_rejected(supervisor, tmp_path):
    result = tmp_path / "result.json"
    atomic_json(result, {"status": "repaired"})
    assert not supervisor.validate_repair(result, supervisor.checkpoint())


def test_wrong_session_rejected(supervisor):
    checkpoint = supervisor.checkpoint()
    checkpoint["session_id"] = "replacement"
    atomic_json(supervisor.config.checkpoint, checkpoint)
    with pytest.raises(ValueError, match="session"):
        supervisor.checkpoint()


def test_failed_repairs_back_off_until_original_cutoff(supervisor, monkeypatch):
    calls = []
    monkeypatch.setattr(supervisor, "watch_game", lambda: "blocked")
    monkeypatch.setattr(supervisor, "repair", lambda reason: calls.append(supervisor.clock()) or False)
    assert supervisor.run() == 0
    assert 1 < len(calls) < 10
    assert calls[1] - calls[0] == 2
    assert supervisor.state["phase"] == "cutoff"
    assert supervisor.clock() == supervisor.state["cutoff"]


def test_game_stopped_before_repair(supervisor, monkeypatch):
    process = FakeProcess()

    def game():
        supervisor.process = process
        return "uncertain"

    def repair(reason):
        assert process.reaped
        assert supervisor.process is None
        supervisor.stop_requested = True
        return False

    monkeypatch.setattr(supervisor, "watch_game", game)
    monkeypatch.setattr(supervisor, "repair", repair)
    assert supervisor.run() == 0


def test_config_rejects_string_command(supervisor):
    supervisor.config.repair_command = "shell command"
    with pytest.raises(ValueError):
        supervisor.config.validate()


def test_independent_review_requires_exact_head_and_other_agent(supervisor):
    path = supervisor.config.state_dir / "review.json"
    atomic_json(path, {"head": "a" * 40, "verdict": "approved",
                      "reviewer": "review-agent", "source_evidence": ["inspected controller safety"]})
    result = {"independent_review": str(path), "repair_agent": "repair-agent"}
    assert supervisor.independent_review(result, "a" * 40)
    assert not supervisor.independent_review(result, "b" * 40)
    result["repair_agent"] = "review-agent"
    assert not supervisor.independent_review(result, "a" * 40)


def test_code_claim_requires_independent_git_and_check_validation(supervisor, tmp_path, monkeypatch):
    result = tmp_path / "result.json"
    atomic_json(result, {
        "status": "repaired", "kind": "code", "session_id": "fresh",
        "checkpoint": str(supervisor.config.checkpoint.resolve()),
        "tests_passed": True, "checks_passed": True, "exact_head_reviewed": True,
        "merged": True, "remotes_synced": True, "commit": "a" * 40,
        "evidence": ["tests and review"], "pr_url": "https://github.com/owner/repo/pull/1",
    })
    monkeypatch.setattr(supervisor, "verify_code", lambda value: False)
    assert not supervisor.validate_repair(result, supervisor.checkpoint())


def test_run_lock_excludes_other_state_directory(supervisor):
    import fcntl

    with supervisor.lock_path().open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Another supervisor"):
            supervisor.run()


def test_corrupt_checkpoint_still_dispatches_repair(supervisor):
    supervisor.config.checkpoint.write_text("{invalid")
    launched = []

    def launch(command, phase, prompt=None):
        launched.append(phase)
        supervisor.process = FakeProcess(0)

    supervisor.launch = launch
    assert not supervisor.repair("checkpoint_invalid")
    assert launched == ["repair"]
    assert supervisor.state["repair_required"] is True
    assert supervisor.state["incident"]["checkpoint"]["session_id"] == "fresh"


def test_rejected_repair_cannot_replace_baseline_on_retry_or_restart(supervisor, monkeypatch):
    initial = supervisor.checkpoint()
    initial.update(pending={"dispatch": "ambiguous"}, active_plan={"id": "original"},
                   step_index=1, reservations={"original": {"iron": 1}})
    atomic_json(supervisor.config.checkpoint, initial)
    supervisor.save(last_valid_checkpoint=initial)
    supervisor.begin_repair("uncertain")
    changed = {**initial, "pending": None, "active_plan": None, "step_index": 0}
    atomic_json(supervisor.config.checkpoint, changed)
    monkeypatch.setattr(supervisor, "source_identity", lambda: ("evil-head", "changed"))
    supervisor.begin_repair("retry")
    supervisor.initialize()
    assert supervisor.state["repair_required"] is True
    assert supervisor.state["incident"]["checkpoint"] == initial
    assert tuple(supervisor.state["incident"]["source"]) == ("head", "diff")


@pytest.mark.parametrize("key,value", [
    ("active_plan", {"id": "changed"}), ("step_index", 99), ("reservations", {}),
])
def test_pending_semantics_cannot_be_changed(supervisor, tmp_path, key, value):
    previous = supervisor.checkpoint()
    previous.update(pending={"dispatch": "ambiguous"}, active_plan={"id": "original"},
                    step_index=1, reservations={"original": {"iron": 1}})
    changed = {**previous, key: value}
    atomic_json(supervisor.config.checkpoint, changed)
    result = operational_result(supervisor, tmp_path)
    assert not supervisor.validate_repair(result, previous, ("head", "diff"))


def test_resume_with_repair_gate_never_starts_game(supervisor, monkeypatch):
    supervisor.begin_repair("blocked")
    monkeypatch.setattr(supervisor, "watch_game", lambda: pytest.fail("repair gate bypassed"))

    def repair(reason):
        assert reason == "blocked"
        supervisor.stop_requested = True
        return False

    monkeypatch.setattr(supervisor, "repair", repair)
    assert supervisor.run() == 0


def test_code_verification_rejects_dirty_worktree(supervisor, monkeypatch):
    calls = iter([(0, "a" * 40), (0, " M src/changed.py")])
    monkeypatch.setattr(supervisor, "capture", lambda command: next(calls))
    assert not supervisor.verify_code({"commit": "a" * 40})


@pytest.mark.parametrize("status,head", [(" M source.py", "a" * 40), ("", "b" * 40)])
def test_code_verification_rechecks_source_after_tests(supervisor, monkeypatch, status, head):
    commit = "a" * 40
    pull = {"state": "MERGED", "mergeCommit": {"oid": commit}, "headRefOid": "c" * 40,
            "reviews": [{"author": {"login": "reviewer"}, "state": "APPROVED",
                         "commit": {"oid": "c" * 40}}],
            "statusCheckRollup": [{"conclusion": "SUCCESS"}]}
    calls = iter([
        (0, commit), (0, ""), (0, "origin\nfork"), (0, commit + "\trefs/heads/main"),
        (0, commit + "\trefs/heads/main"), (0, json.dumps(pull)),
        (0, "passed"), (0, status), (0, head),
    ])
    monkeypatch.setattr(supervisor, "capture", lambda command: next(calls))
    assert not supervisor.verify_code({"commit": commit, "pr_url": "https://github.com/o/r/pull/1"})


def test_crash_after_pending_write_uses_latest_checkpoint(supervisor):
    pending = {**supervisor.checkpoint(), "pending": {"dispatch": "ambiguous"},
               "active_plan": {"id": "latest"}, "step_index": 2,
               "reservations": {"latest": {"iron": 1}}}

    def start(*args, **kwargs):
        atomic_json(supervisor.config.checkpoint, pending)
        return FakeProcess(1)

    supervisor.popen = start
    assert supervisor.watch_game() == "process_exit: 1"
    supervisor.stop_process()
    supervisor.begin_repair("process_exit")
    assert supervisor.state["incident"]["checkpoint"] == pending


def test_final_checkpoint_after_stop_wins_over_last_poll(supervisor):
    latest = {**supervisor.checkpoint(), "pending": {"dispatch": "prepared"}}
    atomic_json(supervisor.config.checkpoint, latest)
    supervisor.begin_repair("heartbeat_timeout")
    assert supervisor.state["incident"]["checkpoint"] == latest


@pytest.mark.parametrize("invalid", [[], None, 1, "wrong"])
def test_non_object_checkpoint_is_repairable(supervisor, invalid):
    supervisor.config.checkpoint.write_text(json.dumps(invalid))
    supervisor.initialize()
    assert supervisor.state["repair_required"]


@pytest.mark.parametrize('remotes,fork_head,accepted', [
    ('origin', 'a' * 40, True),
    ('origin\nfork', 'a' * 40, True),
    ('origin\nfork', 'b' * 40, False),
    ('fork', 'a' * 40, False),
    ('', 'a' * 40, False),
])
def test_code_verification_requires_origin_and_checks_configured_fork(
        supervisor, monkeypatch, remotes, fork_head, accepted):
    commit = 'a' * 40
    pull = {'state': 'MERGED', 'mergeCommit': {'oid': commit}, 'headRefOid': 'c' * 40,
            'reviews': [{'author': {'login': 'reviewer'}, 'state': 'APPROVED',
                         'commit': {'oid': 'c' * 40}}],
            'statusCheckRollup': [{'conclusion': 'SUCCESS'}]}
    seen = []
    def capture(command):
        seen.append(command)
        if command == ['git', 'rev-parse', 'HEAD']: return 0, commit
        if command == ['git', 'status', '--porcelain']: return 0, ''
        if command == ['git', 'remote']: return 0, remotes
        if command[:2] == ['git', 'ls-remote']:
            assert command[2] in remotes.splitlines()
            return 0, (fork_head if command[2] == 'fork' else commit) + '\trefs/heads/main'
        if command[:3] == ['gh', 'pr', 'view']: return 0, json.dumps(pull)
        if command == [supervisor.config.python, '-m', 'pytest', 'tests/']: return 0, 'passed'
        pytest.fail(f'Unexpected command: {command}')
    monkeypatch.setattr(supervisor, 'capture', capture)
    assert supervisor.verify_code({'commit': commit, 'pr_url': 'https://github.com/o/r/pull/1'}) is accepted
    assert ([supervisor.config.python, '-m', 'pytest', 'tests/'] in seen) is accepted


def test_accepted_repair_resumes_without_failure_backoff(supervisor, monkeypatch):
    starts = []
    def watch():
        starts.append(supervisor.clock())
        return 'blocked' if len(starts) == 1 else 'completed'
    def repair(reason):
        supervisor.state['repair_required'] = False
        return True
    monkeypatch.setattr(supervisor, 'watch_game', watch)
    monkeypatch.setattr(supervisor, 'repair', repair)
    assert supervisor.run() == 0
    assert len(starts) == 2 and starts[1] == starts[0]


def test_verification_capture_polls_short_commands_without_full_poll_delay(supervisor, monkeypatch):
    start = supervisor.clock()
    process = FakeProcess()
    process.poll = lambda: 0 if supervisor.clock() >= start + 0.1 else None
    def launch(command, phase):
        assert phase == 'verification'
        supervisor.process = process
        (supervisor.config.state_dir / 'verification.log').write_text('verified')
    monkeypatch.setattr(supervisor, 'launch', launch)
    assert supervisor.capture(['git', 'status']) == (0, 'verified')
    assert supervisor.clock() - start == 0.25


@pytest.mark.parametrize('stop', [False, True])
def test_verification_fast_poll_preserves_cutoff_and_stop(supervisor, monkeypatch, stop):
    start = supervisor.clock()
    supervisor.state['cutoff'] = start + 0.4
    process = FakeProcess()
    def launch(command, phase):
        supervisor.process = process
        (supervisor.config.state_dir / 'verification.log').write_text('')
    def sleep(seconds):
        supervisor.clock.sleep(seconds)
        if stop:
            supervisor.stop_requested = True
    monkeypatch.setattr(supervisor, 'launch', launch)
    supervisor.sleep = sleep
    assert supervisor.capture(['test']) == (None, '')
    assert process.reaped
    assert supervisor.clock() - start == pytest.approx(0.25 if stop else 0.4)
