"""Compose opt-in throughput evidence with every existing controller capability."""
from __future__ import annotations

from copy import deepcopy

from .campaign_progress import ProgressMonitor, ReplenishmentHistory
from .observation import host_pressure
from .skills import Plan


def campaign_loop_type(base):
    class CampaignThroughputLoop(base):
        def __init__(self, *args, lead_time_supply=False, coverage_margin_lookahead=False, **kwargs):
            self._lead_time_supply = lead_time_supply
            self._coverage_margin_lookahead = coverage_margin_lookahead
            self._progress_monitor = ProgressMonitor()
            self._supply_history = ReplenishmentHistory()
            self._campaign_profiles = []
            self._campaign_report = {}
            self._campaign_snapshot = None
            super().__init__(*args, **kwargs)

        def step(self):
            self._campaign_profiles = []
            return super().step()

        def _observe_snapshot(self):
            snapshot = super()._observe_snapshot()  # original identity, budgets and receipt boundaries
            snapshot._campaign_diagnostics = True
            snapshot._lead_time_supply = self._lead_time_supply
            snapshot._coverage_margin_lookahead = self._coverage_margin_lookahead
            if self.catalog:
                snapshot._measured_replenishment = self._supply_history.observe(snapshot, self.catalog)
            self._campaign_snapshot = snapshot
            profile = getattr(self.backend, "last_observation_profile", None)
            if profile:
                self._campaign_profiles.append(deepcopy(profile))
                self._campaign_profiles = self._campaign_profiles[-4:]
            return snapshot

        def _finish_attempt(self, snapshot, outcome="verified"):
            attempt = deepcopy(self.memory.attempt)
            plan = Plan.from_dict(self.memory.active_plan) if self.memory.active_plan else None
            step = plan.steps[self.memory.step_index] if plan else None
            super()._finish_attempt(snapshot, outcome)
            if outcome == "verified" and step and attempt:
                self._supply_history.delivered(step, snapshot, attempt["id"])

        def _record(self, before, action, outcome, after=None, verified=False):
            snapshot = after or before
            self._campaign_snapshot = snapshot
            self._background_evidence = deepcopy(getattr(before, "_background_eligibility", {}))
            self._campaign_report = self._progress_monitor.update(
                snapshot, self.catalog, action, self.memory.status, self._phases,
                self._supply_history.deliveries)
            return super()._record(before, action, outcome, after, verified)

        def _record_extras(self):
            result = super()._record_extras()
            snapshot = self._campaign_snapshot
            from .planning.scheduling import research_schedule, future_research_eligibility
            schedule = research_schedule(snapshot, self.catalog) if snapshot and self.catalog else []
            eligibility = future_research_eligibility(snapshot, self.catalog) if snapshot and self.catalog else {"reason": "no_catalog"}
            active = self.memory.active_plan or {}
            state = self.memory.capital_investment or {}
            role = state.get("spec", {}).get("role")
            entity = snapshot.factory.get("entities", {}).get(role, {}) if snapshot else {}
            # Show the exact bounded failure epoch; do not reset it or manufacture retry eligibility.
            failed = [{"plan_id": key, "failures": count} for key, count in
                      sorted(self.memory.failures.items(), key=lambda row: (-row[1], row[0]))[:16] if count]
            result.update(campaign_treatment={
                "schema": 1, "lead_time_supply": self._lead_time_supply,
                "coverage_margin_lookahead": self._coverage_margin_lookahead,
                "profile_observations": bool(getattr(self.backend, "profile_observations", False)),
                "consolidated_observations": bool(getattr(self.backend, "consolidated_observations", False))},
                campaign_progress=deepcopy(self._campaign_report),
                observation_profiles=deepcopy(self._campaign_profiles), host_pressure=host_pressure(),
                background_eligibility=deepcopy(getattr(self, "_background_evidence", {})),
                research_supply={"schedule": schedule, "measured_replenishment": self._supply_history.evidence(snapshot.tick) if snapshot else {},
                                 "lookahead": eligibility},
                investment_diagnostics={"active_key": state.get("spec", {}).get("key"),
                    "stage": state.get("stage"), "plan_id": active.get("id"), "step_index": self.memory.step_index,
                    "surviving_paid_role": role if entity else None, "unit_number": entity.get("unit_number"),
                    "blocked_plans": failed, "pending_dispatch": (self.memory.pending or {}).get("dispatch"),
                    "retry_authorized": False, "retry_requires": "reviewed_environment_change_and_reconciled_receipts",
                    "revision": self.provenance.get("code_revision"),
                    "recent_failures": [deepcopy(row) for row in self.memory.history[-16:]
                                        if row.get("kind") in {"plan_failed", "capital_abandoned"}]})
            return result

    return CampaignThroughputLoop
