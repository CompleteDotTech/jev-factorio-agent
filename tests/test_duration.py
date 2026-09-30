from jev_factorio import loop
import pytest


def test_duration_runs_without_step_limit_and_stops_at_deadline(monkeypatch):
    clock = [0.0]
    decisions = []
    monkeypatch.setattr(loop.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(loop.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    agent = loop.AgentLoop(object(), jev=object(), tick_seconds=2)
    monkeypatch.setattr(agent, "step", lambda: decisions.append(clock[0]))
    agent.run(steps=None, duration_seconds=25)
    assert len(decisions) == 13
    assert clock[0] == 25


def test_duration_checks_deadline_after_slow_step(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(loop.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(loop.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    agent = loop.AgentLoop(object(), jev=object(), tick_seconds=2)
    monkeypatch.setattr(agent, "step", lambda: clock.__setitem__(0, clock[0] + 10))
    agent.run(steps=None, duration_seconds=5)
    assert clock[0] == 10


def test_duration_retries_transient_api_failure_within_deadline(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(loop.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(loop.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    agent = loop.AgentLoop(object(), jev=object(), tick_seconds=2)

    def step():
        calls.append(clock[0])
        if len(calls) == 1:
            raise loop.requests.Timeout()

    monkeypatch.setattr(agent, "step", step)
    agent.run(steps=None, duration_seconds=33)
    assert calls == [0, 30, 32]
    assert clock[0] == 33


def test_default_run_remains_bounded_to_ten_steps(monkeypatch):
    calls = []
    agent = loop.AgentLoop(object(), jev=object(), tick_seconds=0)
    monkeypatch.setattr(agent, "step", lambda: calls.append("step") or {})
    monkeypatch.setattr(loop, "loop_sleep", lambda *args: None)
    agent.run()
    assert len(calls) == 10


def test_until_complete_runs_to_terminal_without_sleeping_after_terminal(monkeypatch):
    calls = []
    sleeps = []
    agent = loop.AgentLoop(object(), jev=object(), tick_seconds=2)
    agent.terminal = False

    def step():
        calls.append("step")
        agent.terminal = len(calls) == 12
        return {}

    monkeypatch.setattr(agent, "step", step)
    monkeypatch.setattr(loop, "loop_sleep", lambda *args: sleeps.append(args[1]))
    agent.run(until_complete=True)
    assert len(calls) == 12  # The mode is not silently capped at the default ten steps.
    assert sleeps == [2] * 11


def test_until_complete_requires_terminal_controller_and_rejects_bounds_or_gate():
    agent = loop.AgentLoop(object(), jev=object(), tick_seconds=0)
    with pytest.raises(ValueError, match="terminal status"):
        agent.run(until_complete=True)

    agent.terminal = False
    for limits in (
        {"steps": 1},
        {"duration_seconds": 1},
        {"after_step": lambda *args: True},
    ):
        with pytest.raises(ValueError):
            agent.run(until_complete=True, **limits)


def test_until_complete_does_not_add_transient_api_retries(monkeypatch):
    calls = []
    agent = loop.AgentLoop(object(), jev=object(), tick_seconds=0)
    agent.terminal = False

    def step():
        calls.append("step")
        raise loop.requests.Timeout()

    monkeypatch.setattr(agent, "step", step)
    monkeypatch.setattr(loop, "loop_sleep", lambda *args: pytest.fail("unexpected retry sleep"))
    with pytest.raises(loop.requests.Timeout):
        agent.run(until_complete=True)
    assert calls == ["step"]
