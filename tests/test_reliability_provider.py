"""No HTTP/network calls: recovery, persistence, schema and redaction contracts."""
from copy import deepcopy
import json

import pytest
import requests

from jev_factorio.controller import HierarchicalLoop
from jev_factorio.jev_client import JevClient, MockJevClient
from jev_factorio.operational_safety import SafetyStateError, atomic_json
from jev_factorio.provider_health import ProviderBlocked, ProviderCircuit, ProviderPayloadError
from test_provider_failures import CountingBackend, http_error

QUESTIONS = {"ready": {"type": "choice", "criteria": {"yes": "ready"}, "instructions": "choose yes"}}


class Clock:
    now = 1000.0
    def __call__(self):
        return self.now


class Client:
    model = "same-pinned-model"
    base_url = "https://provider.example.invalid"
    answer_quantum = 0
    def __init__(self, error=None):
        self.error = error
        self.calls = 0
    def evaluate(self, state, questions):
        self.calls += 1
        if self.error:
            raise self.error
        return MockJevClient().evaluate(state, questions)


@pytest.mark.parametrize("status,maximum,kind", [
    (401, 3, "authentication_authorization"), (403, 3, "authentication_authorization"),
    (402, 3, "account_quota"), (429, 8, "rate_limit"), (503, 8, "service_network"),
    (400, 1, "application_schema"), (404, 1, "application_schema"),
])
def test_prolonged_failure_budget_survives_every_restart(tmp_path, status, maximum, kind):
    clock = Clock()
    client = Client(http_error(status))
    path = tmp_path / "provider.json"
    for number in range(maximum):
        circuit = ProviderCircuit(client, path, clock=clock)
        with pytest.raises(ProviderBlocked) as raised:
            circuit.evaluate({}, QUESTIONS)
        assert raised.value.called
        assert raised.value.state["category"] == kind
        with pytest.raises(ProviderBlocked) as suppressed:
            circuit.evaluate({}, QUESTIONS)
        assert not suppressed.value.called
        clock.now += 4000
    assert client.calls == maximum
    for _ in range(10):
        with pytest.raises(ProviderBlocked) as raised:
            ProviderCircuit(client, path, clock=clock).evaluate({}, QUESTIONS)
        assert not raised.value.called
    assert client.calls == maximum


@pytest.mark.parametrize("error", [http_error(403), http_error(429), requests.Timeout("secret")])
def test_successful_same_provider_probe_recovers_automatically(tmp_path, error):
    clock = Clock()
    client = Client(error)
    circuit = ProviderCircuit(client, tmp_path / "provider.json", clock=clock)
    with pytest.raises(ProviderBlocked):
        circuit.evaluate({}, QUESTIONS)
    incident = circuit.state["incident_id"]
    client.error = None
    clock.now += 4000
    assert circuit.evaluate({}, QUESTIONS)["ready"]["choice"] == "yes"
    assert circuit.state["phase"] == "healthy"
    assert circuit.state["previous_incident"]["incident_id"] == incident
    assert circuit.state["last_recovery_at"] == clock.now
    assert ProviderCircuit(client, circuit.path).state["phase"] == "healthy"


def test_invalid_answer_cannot_clear_denial_circuit(tmp_path):
    clock = Clock(); client = Client(http_error(403))
    circuit = ProviderCircuit(client, tmp_path / "provider.json", clock=clock)
    with pytest.raises(ProviderBlocked): circuit.evaluate({}, QUESTIONS)
    client.evaluate = lambda *a: {"ready": {"type": "choice", "choice": "yes"}}
    clock.now += 4000
    with pytest.raises(ProviderBlocked): circuit.evaluate({}, QUESTIONS)
    assert circuit.state["phase"] == "exhausted"
    assert circuit.state["category"] == "application_schema"


def test_malformed_response_envelope_is_explicitly_blocked(monkeypatch):
    response = requests.Response(); response.status_code = 200; response._content = b"[]"
    monkeypatch.setattr(requests, "post", lambda *a, **k: response)
    circuit = ProviderCircuit(JevClient(api_key="DO-NOT-LOG-THIS"))
    with pytest.raises(ProviderBlocked) as result:
        circuit.evaluate({}, QUESTIONS)
    assert result.value.state["category"] == "application_schema"
    assert "DO-NOT-LOG-THIS" not in str(result.value) + json.dumps(result.value.state)


def test_retry_after_is_bounded_and_respected():
    clock = Clock(); error = http_error(429); error.response.headers["Retry-After"] = "120"
    circuit = ProviderCircuit(Client(error), clock=clock)
    with pytest.raises(ProviderBlocked): circuit.evaluate({}, QUESTIONS)
    assert circuit.state["next_probe_at"] == 1120


def test_operator_authorization_retains_previous_probe_history(tmp_path):
    clock = Clock(); client = Client(http_error(400))
    circuit = ProviderCircuit(client, tmp_path / "provider.json", clock=clock)
    with pytest.raises(ProviderBlocked): circuit.evaluate({}, QUESTIONS)
    previous = deepcopy(circuit.state)
    request = {"request_id": "11111111-1111-4111-8111-111111111111",
               "incident_id": circuit.state["incident_id"], "evidence_sha256": "a" * 64}
    atomic_json(tmp_path / "provider-authorization.json", request)
    client.error = None
    assert circuit.evaluate({}, QUESTIONS)
    history = json.loads((tmp_path / ("provider-authorized-" + request["request_id"] + ".json")).read_text())
    assert history["previous_state"] == previous
    assert circuit.state["phase"] == "healthy"


def test_provider_circuit_write_failure_prevents_retry(tmp_path, monkeypatch):
    clock = Clock(); client = Client(http_error(403))
    circuit = ProviderCircuit(client, tmp_path / "provider.json", clock=clock)
    with pytest.raises(ProviderBlocked): circuit.evaluate({}, QUESTIONS)
    clock.now += 4000
    monkeypatch.setattr("jev_factorio.provider_health.atomic_json", lambda *a: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError): circuit.evaluate({}, QUESTIONS)
    assert client.calls == 1


def test_controller_recovers_without_deterministic_fallback_on_denial(tmp_path, monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        response = requests.Response()
        if len(calls) == 1:
            response.status_code = 403
        else:
            response.status_code = 200
            response._content = json.dumps({"answers": MockJevClient().evaluate(
                kwargs["json"]["state"], kwargs["json"]["questions"])}).encode()
        return response
    monkeypatch.setattr(requests, "post", post)
    backend = CountingBackend()
    loop = HierarchicalLoop(backend, JevClient(api_key="secret"), policy="hybrid",
        target="bootstrap_mining", checkpoint=str(tmp_path / "cp.json"), tick_seconds=0)
    clock = Clock(); loop.jev.clock = clock
    assert loop.step()["action"] == "observe"
    assert backend.actions == []
    for _ in range(5): loop.step()
    assert len(calls) == 1 and loop.memory.stalled_decisions == 0
    clock.now += 60
    assert loop.step()["action"] != "observe"
    assert backend.actions and loop.jev.state["phase"] == "healthy"
