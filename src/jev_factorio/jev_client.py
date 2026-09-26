"""Thin Jev client: TypeSafe SDK if available, raw HTTP otherwise, mock if no key.

Direct API:  POST https://api.typesafe.ai/v1/systemone  (Bearer TYPESAFE_API_KEY)
             model alias "jev-latest"; docs: https://docs.typesafe.ai/api.md
Gateway:     Vercel AI Gateway, model id "typesafe-ai/jev",
             $0.042 / 1M input tokens, zero output-token charge.
"""
from __future__ import annotations

import os

import requests

from .provider_health import ProviderPayloadError

API_URL = "https://api.typesafe.ai/v1/systemone"
GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/systemone"  # confirm path against Gateway docs


class JevClient:
    uses_http_provider = True
    answer_quantum = 0.01

    def __init__(self, api_key: str | None = None, base_url: str = API_URL,
                 model: str = "jev-latest"):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        self.base_url = base_url
        self.model = model

    def evaluate(self, state: dict, questions: dict) -> dict:
        """One system-one call. Returns the `answers` map keyed by question id."""
        self.last_usage = None
        self.last_model = None
        resp = requests.post(
            self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"state": state, "model": self.model, "questions": questions},
            timeout=10,
        )
        resp.raise_for_status()
        try:
            body = resp.json()
            if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
                raise ProviderPayloadError("Invalid provider response envelope")
        except (ValueError, TypeError) as error:
            raise ProviderPayloadError("Invalid provider response envelope") from error
        self.last_usage = body.get("usage")
        self.last_model = body.get("model")
        return body["answers"]


class MockJevClient:
    """Offline stand-in with the same interface: deterministic, rule-based answers.

    Lets the whole loop run (and tests pass) with no API key and no spend.
    """

    is_mock = True
    model = "mock-rule-based"

    def evaluate(self, state: dict, questions: dict) -> dict:
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "choice":
                pick = next(iter(q["criteria"]))
                # tiny heuristic so the mock loop makes visible progress
                if qid == "next_action":
                    for preferred in ("fuel_drill", "place_burner_drill",
                                      "mine_coal", "mine_iron",
                                      "walk_to_coal", "walk_to_iron"):
                        if preferred in q["criteria"]:
                            pick = preferred
                            break
                answers[qid] = {"type": "choice", "choice": pick,
                                "probabilities": {k: (1.0 if k == pick else 0.0)
                                                  for k in q["criteria"]},
                                "confidence": 0.9}
            elif q["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": 0.0}
            else:
                legend = {str(i): lvl for i, lvl in enumerate(q["criteria"])}
                level = len(legend) - 1 if qid.endswith("/benefit") else 0
                answers[qid] = {"type": "score", "score": float(level), "legend": legend,
                                "probabilities": {k: float(k == str(level)) for k in legend},
                                "confidence": 1.0}
        return answers


class CloudflareJevClient:
    """Jev via Cloudflare Workers AI / AI Gateway.

    POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run
    Body: {"model": "typesafe/jev", "input": {state, questions}}
    - different envelope from api.typesafe.ai (state/questions nest under
      `input`, model id is "typesafe/jev"); answers come back under
      result.result (verified against the Cloudflare model docs and a live
      account, Sept 2026).
    - BILLING: third-party provider models like typesafe/jev are billed via
      AI Gateway Unified Billing (prepaid credits, +5% load fee), NOT the
      10,000 free Neurons/day (those cover first-party @cf/ models only).
      Error code 2021 = insufficient gateway credits.
    Docs: https://developers.cloudflare.com/ai/models/typesafe/jev/
          https://developers.cloudflare.com/ai-gateway/features/unified-billing/
    """

    uses_http_provider = True

    def __init__(self, account_id: str, api_token: str,
                 model: str = "typesafe/jev"):
        self.account_id = account_id
        self.api_token = api_token
        self.model = model
        self.url = (f"https://api.cloudflare.com/client/v4/accounts/"
                    f"{account_id}/ai/run")

    def evaluate(self, state: dict, questions: dict) -> dict:
        self.last_usage = None
        self.last_model = None
        resp = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_token}"},
            json={"model": self.model,
                  "input": {"state": state, "questions": questions}},
            timeout=10,
        )
        resp.raise_for_status()
        try:
            body = resp.json()
            if not isinstance(body, dict) or body.get("success") is not True:
                raise ProviderPayloadError("Provider rejected application request")
            result = body.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
                raise ProviderPayloadError("Invalid provider response envelope")
        except (ValueError, TypeError) as error:
            raise ProviderPayloadError("Invalid provider response envelope") from error
        self.last_usage = result.get("usage")
        self.last_model = result.get("model")
        return result["answers"]


def make_client(*, allow_mock: bool = True, model: str | None = None) -> object:
    """Real client when a key exists, mock otherwise.

    Priority: TypeSafe direct key, then Cloudflare Workers AI token.
    """
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return JevClient(api_key=key, model=model or "jev-latest")
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    cf_acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if cf_token and cf_acct:
        return CloudflareJevClient(account_id=cf_acct, api_token=cf_token,
                                   model=model or "typesafe/jev")
    if not allow_mock:
        raise ValueError("Live Jev credentials are required; use an explicit offline mock")
    return MockJevClient()
