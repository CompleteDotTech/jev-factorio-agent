"""Bounded, durable provider circuit; inference recovery never mutates the game."""
from __future__ import annotations

import hashlib
import json
import math
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from uuid import uuid4

import requests

from .operational_safety import SafetyStateError, atomic_json, read_json


class ProviderPayloadError(ValueError):
    pass


class ProviderBlocked(RuntimeError):
    def __init__(self, state: dict, *, called: bool):
        self.state, self.called = dict(state), called
        super().__init__("Provider access blocked: " + state["category"])


def category(error: BaseException) -> str | None:
    if isinstance(error, ProviderPayloadError):
        return "application_schema"
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return "service_network"
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else None
        if status in (401, 403):
            return "authentication_authorization"
        if status == 402:
            return "account_quota"
        if status == 429:
            return "rate_limit"
        if status == 408 or isinstance(status, int) and 500 <= status <= 599:
            return "service_network"
        return "application_schema"
    return None


class ProviderCircuit:
    """No sleeping inside evaluate: owned/background actions remain observable.

    Three denial/quota probes total, eight transient/rate-limit probes total,
    one schema failure. Exhaustion is persisted across process restarts. A new
    request is never a mutation and is never evidence that credentials changed.
    """
    def __init__(self, client, path: Path | None = None, *, clock=time.time):
        self.client, self.path, self.clock = client, path, clock
        identity = json.dumps([getattr(client, "base_url", getattr(client, "url", "")),
                               getattr(client, "model", "")])
        self.identity = hashlib.sha256(identity.encode()).hexdigest()
        self.state = {"schema": 1, "identity": self.identity, "phase": "healthy",
                      "category": None, "attempts": 0, "next_probe_at": 0,
                      "incident_id": None, "first_failure_at": None,
                      "last_recovery_at": None, "previous_incident": None}
        if path is not None:
            stored = read_json(path)
            if stored is not None:
                self._validate(stored)
                self.state = stored

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _validate(self, value):
        if (value.get("schema") != 1 or value.get("identity") != self.identity
                or value.get("phase") not in {"healthy", "cooldown", "exhausted"}
                or type(value.get("attempts")) is not int or value["attempts"] < 0
                or type(value.get("next_probe_at")) not in (int, float)
                or not math.isfinite(value["next_probe_at"])
                or value.get("category") not in {None, "application_schema", "service_network",
                    "authentication_authorization", "account_quota", "rate_limit"}):
            raise SafetyStateError("Provider circuit identity or schema differs")

    def _save(self):
        if self.path is not None:
            atomic_json(self.path, self.state)

    def evaluate(self, state: dict, questions: dict) -> dict:
        self.client.last_usage = self.client.last_model = None
        now = self.clock()
        self._authorization()
        if self.state["phase"] == "exhausted" or now < self.state["next_probe_at"]:
            raise ProviderBlocked(self.state, called=False)
        # Reserve this probe durably before the HTTP call. A process crash cannot
        # reset the attempt budget or immediately repeat a denied request.
        if self.state["phase"] != "healthy":
            self.state["attempts"] += 1
            maximum = self._limit(self.state["category"])
            self.state["phase"] = "exhausted" if self.state["attempts"] >= maximum else "cooldown"
            self.state["next_probe_at"] = now + self._delay(self.state["category"], self.state["attempts"])
            self._save()
        try:
            answers = self.client.evaluate(state, questions)
            from .judgments import InvalidJudgment, validate_answers
            try:
                validate_answers(questions, answers, quantum=getattr(self.client, "answer_quantum", 0))
            except InvalidJudgment as error:
                raise ProviderPayloadError("Provider answer schema rejected") from error
        except (requests.RequestException, ProviderPayloadError) as error:
            kind = category(error)
            if kind is None:
                raise
            if self.state["phase"] == "healthy":
                self.state.update(incident_id=str(uuid4()), first_failure_at=now, attempts=1)
            attempts = self.state["attempts"]
            delay = self._delay(kind, attempts)
            response = error.response if isinstance(error, requests.HTTPError) else None
            if response is not None:
                try:
                    retry_after = float(response.headers.get("Retry-After", "0"))
                    if math.isfinite(retry_after):
                        delay = max(delay, min(3600, max(0, retry_after)))
                except (ValueError, TypeError):
                    try:
                        retry_at = parsedate_to_datetime(response.headers.get("Retry-After", "")).timestamp()
                        delay = max(delay, min(3600, max(0, retry_at - now)))
                    except (ValueError, TypeError, OverflowError):
                        pass
            self.state.update(category=kind, phase="exhausted" if attempts >= self._limit(kind) else "cooldown",
                              next_probe_at=now + delay)
            self.client.last_usage = self.client.last_model = None
            self._save()
            raise ProviderBlocked(self.state, called=True) from None
        if self.state["phase"] != "healthy":
            self.state.update(previous_incident={key: self.state[key] for key in
                              ("incident_id", "category", "attempts", "first_failure_at")},
                              phase="healthy", category=None, attempts=0, next_probe_at=0,
                              last_recovery_at=now, incident_id=None, first_failure_at=None)
            self._save()
        return answers

    def _authorization(self):
        if self.path is None or self.state["phase"] == "healthy":
            return
        request = read_json(self.path.parent / "provider-authorization.json")
        if request is None or request.get("request_id") == self.state.get("authorization_id"):
            return
        request_id, evidence = request.get("request_id"), request.get("evidence_sha256")
        if (request.get("incident_id") != self.state["incident_id"]
                or not isinstance(request_id, str) or len(request_id) != 36
                or any(c not in "0123456789abcdef-" for c in request_id)
                or not isinstance(evidence, str) or len(evidence) != 64
                or any(c not in "0123456789abcdef" for c in evidence)):
            raise SafetyStateError("Provider probe authorization does not match this incident")
        # Immutable history first, then an atomic new generation of probe budget.
        history_path = self.path.parent / ("provider-authorized-" + request_id + ".json")
        history = {"request": request, "previous_state": self.state}
        existing = read_json(history_path)
        if existing is not None and existing != history:
            raise SafetyStateError("Provider authorization history conflict")
        if existing is None:
            atomic_json(history_path, history)
        self.state = {**self.state, "phase": "cooldown", "attempts": 0,
                      "next_probe_at": 0, "authorization_id": request_id}
        self._save()

    @staticmethod
    def _limit(kind):
        return 1 if kind == "application_schema" else 3 if kind in {
            "authentication_authorization", "account_quota"} else 8

    @staticmethod
    def _delay(kind, attempt):
        base = 60 if kind in {"authentication_authorization", "account_quota"} else 5
        return min(900, base * 2 ** min(attempt - 1, 8))
