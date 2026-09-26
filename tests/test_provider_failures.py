import pytest
import requests

from jev_factorio.backends.mock import MockBackend
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.jev_client import CloudflareJevClient, JevClient
from jev_factorio.judgments import select_plan
from jev_factorio.skills import compile_plans


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError("provider error", response=response)


class CountingBackend(MockBackend):
    def __init__(self):
        super().__init__()
        self.actions = []

    def act(self, action):
        self.actions.append(action)
        return super().act(action)


@pytest.mark.parametrize("error", [
    http_error(529), http_error(503), http_error(408), http_error(429),
    requests.Timeout("secret provider detail"),
    requests.ConnectionError("secret provider detail"),
])
@pytest.mark.parametrize("policy", ["hybrid", "jev"])
def test_transient_provider_failure_respects_policy(monkeypatch, error, policy):
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise error

    monkeypatch.setattr(requests, "post", fail)
    client = JevClient(api_key="test-only")
    client.last_usage = {"input_tokens": 123}
    client.last_model = "previous-success"
    backend = CountingBackend()
    loop = HierarchicalLoop(backend, jev=client, policy=policy, target="bootstrap_mining",
                            max_stalled_decisions=2, tick_seconds=0)
    record = loop.step()
    assert record["model_call"] is True
    assert record["resolved_model"] is None
    assert record["usage"] is None
    decision = record["decision"]
    assert decision["model_called"] is True
    assert decision["answers"] == {}
    assert decision["utilities"] == {}
    assert decision["reason"].startswith("Provider access blocked:")
    assert "secret" not in decision["reason"]
    assert decision["source"] == "observe"
    assert decision["plan_id"] is None
    assert record["action"] == "observe"
    assert backend.actions == []
    assert not loop.terminal
    # Cooldown is operational, not two exhausted gameplay-selection attempts.
    again = loop.step()
    assert not loop.terminal and loop.memory.status == "running"
    assert loop.memory.stalled_decisions == 0 and loop.memory.failures == {}
    assert again["model_call"] is False and len(calls) == 1
    assert backend.actions == []


@pytest.mark.parametrize("error", [
    http_error(400), http_error(401), http_error(403), http_error(404),
    requests.HTTPError("no response"),
    RuntimeError("programmer error"), TypeError("programmer error"),
])
@pytest.mark.parametrize("policy", ["jev", "hybrid"])
def test_denials_block_and_programmer_errors_propagate(monkeypatch, error, policy):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(requests, "post", fail)
    backend = CountingBackend()
    loop = HierarchicalLoop(backend, jev=JevClient(api_key="test-only"), policy=policy,
                            target="bootstrap_mining", tick_seconds=0)
    if isinstance(error, requests.HTTPError):
        record = loop.step()
        assert record["action"] == "observe"
        assert record["decision"]["diagnostics"]["outcome"] == "provider_blocked"
        assert loop.memory.status == "running" and loop.memory.stalled_decisions == 0
        assert not loop.terminal
    else:
        with pytest.raises(type(error)) as caught:
            loop.step()
        assert caught.value is error
    assert backend.actions == []


@pytest.mark.parametrize("client", [
    JevClient(api_key="test-only"),
    CloudflareJevClient(account_id="test-only", api_token="test-only"),
])
def test_all_live_clients_clear_previous_response_metadata(monkeypatch, client):
    client.last_usage = {"input_tokens": 123}
    client.last_model = "previous-success"

    def fail(*args, **kwargs):
        raise requests.Timeout()

    monkeypatch.setattr(requests, "post", fail)
    plans, _ = compile_plans("stockpile_fuel", MockBackend().observe())
    result = select_plan(client, {}, plans)
    assert result.model_called is True
    assert result.plan_id is None
    assert client.last_usage is None
    assert client.last_model is None
