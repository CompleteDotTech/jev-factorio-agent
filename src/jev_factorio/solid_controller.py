"""Explicit experimental composition for paid solid routes.

Not enabled by CLI or supervisor. Native qualification and an authorized immutable
runtime handoff are required before making this a production treatment.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

from . import solid_routes as routes, coal_supply as coal
from .backends.solid_routes import SolidRouteFactory, validate_intents
from .planning.solid_routes import candidates
from .planning import solid_investment, solid_funding
from .planning.demand import SupplyLedger
from .skills import Plan
from .research_log import ResearchLogError, RunConfiguration
from .telemetry import phase

CHECKPOINT_FIELDS = {"solid_routes_schema", "solid_intents", "solid_epoch", "solid_commitments"}
UNBOUND_FAULT = "Solid-route epoch unbound; native reconciliation required"


class SolidRouteMixin:
    _solid_routes_enabled = True

    def __init__(self, backend, jev=None, *, solid_intents, solid_science_policy=False, **options) -> None:
        if type(solid_science_policy) is not bool:
            raise ValueError("Solid science policy must be an explicit boolean")
        self._solid_science_policy = solid_science_policy
        self._solid_policy_evidence = {}
        self._solid_funding_release = None
        self._solid_intents = validate_intents(solid_intents)
        self._solid_fault = False
        self._solid_resume_observing = False
        self._solid_evidence = {}
        if options.get("factory_scheduling") != "ready-work":
            raise ValueError("Solid routes require explicit ready-work scheduling")
        if not options.get("checkpoint"):
            raise ValueError("Solid routes require a durable controller checkpoint")
        sink = options.get("research_log")
        if sink is not None:
            configuration = getattr(sink, "configuration", None)
            if (not isinstance(configuration, RunConfiguration) or configuration.solid_routes is not True
                    or configuration.factory_scheduling != "ready-work"
                    or configuration.solid_science_policy is not solid_science_policy):
                raise ValueError("Research manifest must explicitly declare the solid-route treatment")
        # Validate the entire composed memory, not just its treatment labels,
        # before even enable_factory can install a native extension. The saved
        # session is a schema binding only; the first fresh observation still
        # checks the live session, tick, epoch, ownership and receipts.
        self._solid_resume_checkpoint = None
        self._solid_resume_memory = None
        if options.get("resume_controller"):
            path = Path(options["checkpoint"])
            captured = path.read_bytes()
            saved = json.loads(captured)
            if (not isinstance(saved, dict) or not CHECKPOINT_FIELDS <= saved.keys()
                    or saved["solid_intents"] != self._solid_intents
                    or saved.get("solid_science_policy", False) is not solid_science_policy):
                raise ValueError("Solid treatment cannot silently replace or migrate a checkpoint")
            restored = self.memory_type.from_bytes(captured, saved.get("session_id"),
                                                   options.get("target", "rocket_launch"))
            if path.read_bytes() != captured:
                raise ValueError("Checkpoint changed during resume validation")
            if not restored.solid_epoch:
                # This is valid retained fault evidence for offline inspection,
                # not permission to install another runtime or adopt an epoch.
                raise ValueError(UNBOUND_FAULT)
            self._solid_resume_checkpoint = captured
            self._solid_resume_memory = restored
        super().__init__(backend, jev, **options)
        native = getattr(backend, "_factory", None)
        if native is not None:
            current = native
            while current is not None and not isinstance(current, SolidRouteFactory):
                current = getattr(current, "native", None)
            if current is None:
                backend._factory = SolidRouteFactory(native, self._solid_intents)
            elif current.intents != self._solid_intents:
                raise ValueError("Existing native solid treatment differs")
        elif getattr(backend, "solid_routes_supported", False) is not True:
            raise ValueError("Backend does not support owned solid-route observations")

    def _check_resume_checkpoint(self):
        try:
            unchanged = self.checkpoint.read_bytes() == self._solid_resume_checkpoint
        except OSError:
            unchanged = False
        if not unchanged:
            # Do not retain foreign memory or overwrite the other writer's file.
            # A swallowed exception cannot permit a later step/save to continue.
            self.memory = None
            self._solid_fault = True
            self._persistence_failed = True
            raise ValueError("Checkpoint changed after resume validation; reconstruct before continuing")

    def _initial_memory(self, snapshot):
        if self._solid_resume_memory is not None:
            # The preflight used the full composed loader. Reuse those validated
            # bytes' value rather than reopening a mutable path (including ABA).
            # The ordinary observer still checks the live session and tick.
            return deepcopy(self._solid_resume_memory)
        return super()._initial_memory(snapshot)

    def _observe_snapshot(self):
        initial_resume = self.memory is None and self._solid_resume_checkpoint is not None
        if initial_resume:
            self._check_resume_checkpoint()
        # Leave initialization in the ordinary observer so run() still performs
        # its first fresh receipt reconciliation even for a retained terminal state.
        try:
            snapshot = super()._observe_snapshot()
        except BaseException:
            if initial_resume and self.memory is not None:
                # The phase failure callback runs outside this method and may
                # persist diagnostics against any provisional pending attempt.
                # Reject that memory before the callback can write it.
                self.memory = None
                self._solid_fault = True
                self._persistence_failed = True
            raise
        finally:
            # Also runs when an observation/decoder/validator raises. Never let a
            # successful earlier comparison authorize bytes replaced in flight.
            if initial_resume:
                self._check_resume_checkpoint()
        if initial_resume and not self._solid_resume_observing:
            self._solid_resume_memory = None
            self._solid_resume_checkpoint = None
        # Inner composed observers may save their own new ownership before the
        # outer observation completes. Bind this extension first, so a crash
        # there leaves a reloadable checkpoint rather than empty treatment fields.
        if not self.memory.solid_intents:
            self.memory.solid_intents = deepcopy(self._solid_intents)
            self.memory.solid_science_policy = self._solid_science_policy
        if not self.memory.solid_epoch:
            try:
                routes.routes(snapshot)
                self.memory.solid_epoch = {key: snapshot.factory["solid_routes"][key]
                                          for key in ("actor_index", "surface_index", "force_index")}
            except (ValueError, KeyError, TypeError, AttributeError):
                # An inner observer can persist before the outer observer runs.
                # Preserve a reloadable, explicitly unbound fault at that point.
                self._solid_fault = True
                self.memory.status, self.memory.reason = "uncertain", UNBOUND_FAULT
        return snapshot

    def _save(self):
        if self._persistence_failed:
            raise RuntimeError("Checkpoint persistence failed; reconstruct before continuing")
        if self._solid_resume_observing:
            # Only the first read-only resumed observation can defer a save.
            # Inner layers reconcile receipts/ownership, but their tentative
            # state cannot replace the capture until every observer returns.
            # The outer synchronous flush still precedes the next actor action.
            return
        return super()._save()

    def _observe(self, stage="observe"):
        if self._persistence_failed:
            raise RuntimeError("Checkpoint persistence failed; reconstruct before continuing")
        if self._solid_resume_observing:
            raise RuntimeError("Reentrant initial resume observation; reconstruct before continuing")
        initial_resume = self.memory is None and self._solid_resume_checkpoint is not None
        if not initial_resume:
            return self._observe_solid(stage)
        self._solid_resume_observing = True
        try:
            snapshot = self._observe_solid(stage)
            # Cover post-snapshot validation and ownership saves in every inner
            # composed observer, not just the base native read/memory load.
            self._check_resume_checkpoint()
        except BaseException:
            if self.memory is not None:
                # A rejected partially initialized observation cannot be retried
                # or persisted, even after the original pathname is restored.
                self.memory = None
                self._solid_fault = True
                self._persistence_failed = True
            # Preserve the existing retry for a failed read *before* memory was
            # initialized, but only when the validated checkpoint is unchanged.
            self._check_resume_checkpoint()
            raise
        finally:
            self._solid_resume_observing = False
        self._save()  # Exact reconciled state is durable before action admission.
        self._solid_resume_memory = None
        self._solid_resume_checkpoint = None
        return snapshot

    def _observe_solid(self, stage="observe"):
        snapshot = super()._observe(stage)
        try:
            rows = routes.routes(snapshot)
            epoch = {key: snapshot.factory["solid_routes"][key]
                     for key in ("actor_index", "surface_index", "force_index")}
            if self.memory.solid_epoch and self.memory.solid_epoch != epoch:
                raise ValueError("Solid route actor/surface/force changed")
            if (self.memory.solid_intents and self.memory.solid_intents != self._solid_intents
                    or self.memory.solid_science_policy is not self._solid_science_policy):
                raise ValueError("Solid route intent or policy binding changed")
            intents = {(i["source"], i["target"], i["item"], i["destination"]) for i in self._solid_intents}
            for key, saved in self.memory.solid_commitments.items():
                if key not in rows or not routes.reconciles(saved, rows[key]):
                    raise ValueError("Paid solid commitment disappeared or regressed")
            for key, row in rows.items():
                if not routes.current(row, snapshot):
                    raise ValueError("Solid route requires native reconciliation")
                if (row["source"]["role"], row["target"]["role"], row["item"], row["target"]["inventory"]) not in intents:
                    raise ValueError("Unrequested solid route")
                if row["state"] != "proposed":
                    if key not in self.memory.solid_commitments:
                        plan = Plan.from_dict(self.memory.active_plan) if self.memory.active_plan else None
                        step = plan.steps[self.memory.step_index] if plan else None
                        if (not self.memory.pending or step is None or step.action != routes.COMMAND
                                or (step.parameters or {}).get("route") != key
                                or step.parameters.get("layout") != row["layout"]):
                            raise ValueError("Untracked native solid commitment requires reconciliation")
                    self.memory.solid_commitments[key] = routes.commitment(row)
            self.memory.solid_epoch = epoch
            self.memory.solid_intents = deepcopy(self._solid_intents)
        except (ValueError, TypeError, KeyError, AttributeError):
            self._solid_fault = True
            self.memory.status = "uncertain"
            self.memory.reason = ("Solid-route evidence invalid; preserve pending state and ownership"
                                  if self.memory.solid_epoch else UNBOUND_FAULT)
        if not self._solid_fault:
            if self._solid_science_policy:
                for value in routes.routes(snapshot).values():
                    key = solid_funding.project_key(value)
                    if (key not in self.memory.solid_funding_catalogs
                            and len(self.memory.solid_funding_catalogs) < routes.MAX_ROUTES):
                        try:
                            declaration = solid_funding.acquisition_evidence({**value, 'parts': {}}, snapshot, self.catalog, {})
                            self.memory.solid_funding_catalogs[key] = {
                                'schema': 1, 'observed_tick': snapshot.tick, 'version': self.catalog.version,
                                'catalog_sha256': solid_funding.digest(declaration['catalog']),
                                'acquisition_sha256': solid_funding.digest({k: v for k, v in declaration.items() if k != 'reserved'})}
                        except (ValueError, KeyError, TypeError, AttributeError):
                            pass  # Missing audit declaration cannot authorize or block a native action.
            self._reconcile_solid_funding(snapshot)
        self._solid_evidence = deepcopy(snapshot.factory.get("solid_routes", {}))
        self._save()  # Exact paid prefix is durable before another actor mutation.
        return snapshot

    def _commit_solid(self, plan, snapshot):
        marker = (plan.materials or {}).get(solid_investment.MARKER, {})
        if marker.get("stage") != "kit":
            return
        state = self.memory.solid_funding
        if (not self._solid_science_policy or self.memory.pending or self.memory.capital_investment
                or not solid_investment.fresh_permission(plan, plan.steps[0], snapshot, self.catalog,
                    outcomes=self.memory.attempt_outcomes, reserved=self._solid_reservations(),
                    job=getattr(self, "_job", lambda: None)(), funding=state, failures=self.memory.failures)):
            raise ValueError("Cannot commit an unqualified solid kit")
        row = routes.routes(snapshot)[marker["route"]]
        acquisition = solid_funding.acquisition_evidence(row, snapshot, self.catalog, self._solid_reservations())
        if state is None:
            self.memory.solid_funding = solid_funding.start(row, marker, snapshot.tick)
        else:
            if state["actions"] >= solid_funding.MAX_ACTIONS:
                raise ValueError("Solid kit action budget exhausted")
            state["actions"] += 1
        self.memory.event("solid_kit_committed", key=plan.id, tick=snapshot.tick,
                          funding=deepcopy(self.memory.solid_funding), step=asdict(plan.steps[0]),
                          acquisition=deepcopy(acquisition))
        # The ordinary plan-commit save follows before fresh observation and the
        # prepared mutation save. No asynchronous or new durability path exists.

    def _reconcile_solid_funding(self, snapshot):
        state = self.memory.solid_funding
        if state is None or (self._solid_funding_release is not None
                             and self._solid_funding_release[0] != state):
            self._solid_funding_release = None
        if state is None or self.memory.pending:
            return  # Ambiguous native work is reconciled before any release.
        solid_funding.validate_state(state, snapshot.tick, self._solid_intents)
        row = routes.routes(snapshot).get(state["route"])
        if row and row["state"] != "proposed" and state["route"] in self.memory.solid_commitments:
            self.memory.event("solid_kit_paid_handoff", key=state["key"], tick=snapshot.tick,
                              funding=deepcopy(state))
            self.memory.solid_funding = None
            self._solid_funding_release = None
            return
        reason = None
        if not row or not solid_funding.bound(state, row):
            reason = "kit_endpoint_or_layout_changed"
        elif snapshot.tick >= state["deadline_tick"]:
            reason = "kit_deadline"
        elif self.memory.failures.get(state["key"] + ":kit", 0) >= 2:
            reason = "kit_failure_budget"
        elif state["actions"] >= solid_funding.MAX_ACTIONS and self.memory.active_plan is None:
            # A complete kit may still commission normally; otherwise this cap
            # cannot be reset by ingredient changes or a fresh plan ID.
            if any(snapshot.inventory.get(k, 0) < v for k,v in routes.remaining(row).items()):
                reason = "kit_action_budget"
        if reason is None:
            try:
                if solid_funding.catalog_digest(row, snapshot, self.catalog) != state["catalog_sha256"]:
                    reason = "kit_catalog_changed"
                demand, _ = solid_investment.requirements(snapshot, self.catalog,
                    reserved=self._solid_reservations(), job=getattr(self, "_job", lambda: None)())
                if not solid_investment.offer_value(row, snapshot, self.catalog, demand,
                                                    self.memory.attempt_outcomes).get("eligible"):
                    reason = reason or "kit_demand_or_payback_changed"
            except (ValueError, KeyError, TypeError):
                reason = "kit_evidence_unavailable"
        if reason:
            # Preserve this observation's cause through deferred plan cleanup.
            # It is diagnostic process-local state, never checkpoint authority.
            if self._solid_funding_release is None:
                self._solid_funding_release = (deepcopy(state), reason, snapshot.tick)
            reason = self._solid_funding_release[1]
            key = state["key"] + ":kit"
            prior = self.memory.failures.get(key, 0)
            self.memory.failures[key] = max(2, prior)
            # Keep the funding/active-plan binding together across every save.
            # A fresh observer must not clear a plan the base dispatch path is
            # still inspecting. Its ordinary failed-precondition path clears it.
            if (self.memory.active_plan or {}).get("id") != key:
                self.memory.event("solid_kit_abandoned", key=key, reason=reason, tick=snapshot.tick,
                                  funding=deepcopy(state))
                self.memory.solid_funding = None
                self._solid_funding_release = None

    def _clear_plan(self):
        super()._clear_plan()
        state = getattr(self.memory, "solid_funding", None)
        if state and self.memory.failures.get(state["key"] + ":kit", 0) >= 2:
            observed = self._solid_funding_release
            reason = (observed[1] if observed and observed[0] == state
                      and observed[2] <= self.memory.last_tick else "kit_failure_budget")
            self.memory.event("solid_kit_abandoned", key=state["key"] + ":kit",
                              reason=reason, tick=self.memory.last_tick,
                              funding=deepcopy(state))
            self.memory.solid_funding = None
            self._solid_funding_release = None

    def _execution_barrier(self, snapshot):
        return (self._persistence_failed or self._solid_resume_observing or self._solid_fault
                or super()._execution_barrier(snapshot))

    def _step_allowed(self, step, snapshot):
        if self._execution_barrier(snapshot) or not routes.permits(step.action, step.parameters or {}, snapshot):
            return False
        try:
            own = (step.parameters or {}).get("route") if step.action == routes.COMMAND else None
            reserved = Counter()
            # The durable commitment is the kit lock. Reuse SupplyLedger's
            # spendable accounting; clearing a one-component Plan cannot release it.
            for key, saved in self.memory.solid_commitments.items():
                if key != own:
                    reserved.update(routes.remaining(saved))
            active = (self.memory.active_plan or {}).get("id")
            for owner, held in self.memory.reservations.items():
                if owner != active:
                    reserved.update(held)
            job = getattr(self, "_job", lambda: None)()
            ledger = SupplyLedger.capture(snapshot, self.catalog, reserved=dict(reserved), job=job)
            needed = (routes.remaining(routes.routes(snapshot)[own]) if own else step.costs or {})
            if any(ledger.carried.get(item, 0) < count for item, count in needed.items()):
                return False
        except (ValueError, KeyError, TypeError, AttributeError):
            return False
        return super()._step_allowed(step, snapshot)

    def _compile_candidates(self, snapshot):
        plans, blocker = super()._compile_candidates(snapshot)
        if self.memory.active_goal == "bootstrap_mining" or self._execution_barrier(snapshot):
            return plans, blocker
        # Compile the existing production frontier exactly once. Infrastructure
        # is appended, never an exclusive override of science or maintenance.
        if self._solid_science_policy:
            extras, self._solid_policy_evidence = solid_investment.candidates(
                snapshot, self.catalog, self.memory.active_goal,
                outcomes=self.memory.attempt_outcomes, reserved=self._solid_reservations(),
                job=getattr(self, "_job", lambda: None)(),
                capital_active=self.memory.capital_investment is not None,
                funding=self.memory.solid_funding, failures=self.memory.failures)
            # Decision-local ranking evidence, not checkpoint state or a grant.
            snapshot._solid_investment_annotations = {
                plan.id: deepcopy(plan.materials[solid_investment.MARKER]) for plan in extras}
        else:
            extras = candidates(snapshot, self.memory.active_goal)
        merged = plans + [p for p in extras if p.id not in {p.id for p in plans}]
        allowed = [p for p in merged if self._step_allowed(p.steps[0], snapshot)]
        return allowed, blocker if not allowed else ""

    def _solid_reservations(self):
        reserved = Counter()
        for saved in self.memory.solid_commitments.values():
            reserved.update(routes.remaining(saved))
        for held in self.memory.reservations.values():
            reserved.update(held)
        return dict(reserved)

    def _plan_failure_count(self, plan):
        count = super()._plan_failure_count(plan)
        marker = (plan.materials or {}).get(solid_investment.MARKER, {})
        if isinstance(marker, dict) and marker.get("stage") == "kit" and len(plan.steps) == 1:
            return max(count, solid_funding.failure_count(plan, self.memory.failures))
        return count

    def _investment_step_allowed(self, plan, step, snapshot):
        marker = (plan.materials or {}).get(solid_investment.MARKER, {})
        state = self.memory.solid_funding
        if state and plan.id == state["key"] + ":kit" and marker.get("stage") != "kit":
            return False
        if marker.get("stage") == "kit" and (not self._solid_science_policy
                or self.memory.solid_funding is None or self.memory.capital_investment is not None
                or self._plan_failure_count(plan) >= 2
                or plan.id != self.memory.solid_funding["key"] + ":kit"):
            return False
        if self._solid_science_policy and ((step.action == routes.COMMAND
                                          and not coal.is_network_route(step.parameters, snapshot))
                                          or solid_investment.MARKER in (plan.materials or {})):
            # Do not double-reserve the active one-component plan and its paid
            # project's remaining kit while assessing current downstream demand.
            reserved = Counter(self._solid_reservations())
            held = self.memory.reservations.get(plan.id, {})
            reserved.subtract(held)
            if not solid_investment.fresh_permission(
                    plan, step, snapshot, self.catalog, outcomes=self.memory.attempt_outcomes,
                    reserved={key: value for key, value in reserved.items() if value > 0},
                    job=getattr(self, "_job", lambda: None)(), funding=self.memory.solid_funding,
                    failures=self.memory.failures):
                return False
        return super()._investment_step_allowed(plan, step, snapshot)

    def _verify_pending(self, snapshot):
        pending = self.memory.pending
        if pending and not self._execution_barrier(snapshot):
            plan = Plan.from_dict(self.memory.active_plan)
            step = plan.steps[self.memory.step_index]
            row = routes.routes(snapshot).get((step.parameters or {}).get("route"))
            proof = {"part": (step.parameters or {}).get("part"),
                     "receipt": (step.parameters or {}).get("receipt"), "phase": "prepared"}
            # At most one exact idempotent replay, only after native write-ahead
            # evidence proves the ordinary actor placement has not started.
            if (step.action == routes.COMMAND and pending.get("dispatch") in {"prepared", "ambiguous"}
                    and pending.get("polls") == 0 and row and row["pending"] == proof
                    and self._step_allowed(step, snapshot)
                    and self._investment_step_allowed(plan, step, snapshot)):
                pending["polls"] = 1
                self.memory.event("solid_prepared_recovery", plan=plan.id, tick=snapshot.tick)
                self._save()
                try:
                    with phase("dispatch", self._diagnostic_trace):
                        self._trace.dispatch(
                            lambda: (self.backend.execute_traced(step.action, step.parameters, self._diagnostic_trace)
                                     if getattr(self.backend, "execute_traced", None)
                                     else self.backend.execute(step.action, step.parameters)),
                            step.action, parameters=step.parameters, plan_id=plan.id,
                            step_index=self.memory.step_index, pending=pending,
                            checkpointed=True, attempt_id=self.memory.attempt["id"])
                except ResearchLogError:
                    raise
                except Exception:
                    pending["dispatch"] = "ambiguous"
                    self._save()
                    return self._record(snapshot, "observe", "Exact solid replay remains ambiguous; preserve pending receipt")
                pending["dispatch"] = "returned"
                self._save()
                self._trace.observation_phase = "post_recovery_dispatch"
                after = self._observe("post_dispatch_observe")
                return super()._verify_pending(after)
        return super()._verify_pending(snapshot)

    def _record_extras(self):
        return {**super()._record_extras(), "solid_routes": True,
                "solid_route_evidence": deepcopy(self._solid_evidence), "solid_route_fault": self._solid_fault,
                "solid_science_policy": self._solid_science_policy,
                "solid_funding_schema": 1, "solid_funding": deepcopy(self.memory.solid_funding),
                "solid_investment_evidence": deepcopy(self._solid_policy_evidence)}

    def _model_history(self):
        return [{key: value for key, value in event.items()
                 if key in {'kind', 'key', 'tick', 'reason'}}
                if event.get('kind', '').startswith('solid_kit_') else
                {key: deepcopy(value) for key, value in event.items() if key != 'definition'}
                for event in super()._model_history()]

    def _model_facts(self, snapshot):
        facts = super()._model_facts(snapshot)
        summary = {}
        for key, row in routes.routes(snapshot).items():
            for part in row["parts"].values():
                facts["factory"].get("entities", {}).pop(part["role"], None)
            summary[key] = {"item": row["item"], "source": row["source"]["role"],
                            "target": row["target"]["role"], "state": row["state"],
                            "paid_components": len(row["parts"]), "total_components": len(row["steps"]),
                            "reason": row["reason"], "flow_verified": routes.flow_complete(key, row["layout"], snapshot)}
        facts["factory"]["solid_routes"] = {"protocol": 1, "routes": summary,
            "diagnostics": deepcopy(snapshot.factory["solid_routes"]["diagnostics"])}
        return facts


def solid_loop_type(base):
    @dataclass
    class SolidMemory(base.memory_type):
        solid_routes_schema: int = 1
        solid_science_policy: bool = False
        solid_intents: list = field(default_factory=list)
        solid_epoch: dict = field(default_factory=dict)
        solid_commitments: dict = field(default_factory=dict)
        solid_funding: dict | None = None
        solid_funding_catalogs: dict = field(default_factory=dict)

        @classmethod
        def _from_data(cls, data, session_id, target):
            if not isinstance(data, dict) or not CHECKPOINT_FIELDS <= data.keys():
                raise ValueError("Incomplete or legacy solid-route checkpoint")
            memory = super()._from_data(data, session_id, target)
            if not routes.integer(memory.solid_routes_schema, 1, 1):
                raise ValueError("Unsupported solid checkpoint version")
            if type(memory.solid_science_policy) is not bool:
                raise ValueError("Invalid solid policy binding")
            validate_intents(memory.solid_intents)
            solid_funding.validate_catalog_declarations(memory.solid_funding_catalogs, memory.last_tick)
            if memory.solid_funding is not None:
                if not memory.solid_science_policy or memory.capital_investment is not None:
                    raise ValueError("Solid funding conflicts with immutable policy or capital")
                solid_funding.validate_state(memory.solid_funding, memory.last_tick, memory.solid_intents)
            active = Plan.from_dict(memory.active_plan) if memory.active_plan else None
            marker = (active.materials or {}).get(solid_investment.MARKER, {}) if active else {}
            owned_kit = memory.solid_funding and active and active.id == memory.solid_funding["key"] + ":kit"
            if not isinstance(marker, dict):
                raise ValueError("Invalid solid investment checkpoint annotation")
            kit_identity = active and active.id.startswith("solid-project:") and active.id.endswith(":kit")
            if marker.get("stage") == "kit" or owned_kit or kit_identity:
                state = memory.solid_funding
                if (not state or not owned_kit or marker.get("stage") != "kit"
                        or marker.get("route") != state["route"] or marker.get("layout") != state["layout"]
                        or marker.get("catalog_sha256") != state["catalog_sha256"]
                        or type(marker.get("schema")) is not int or marker["schema"] != 1
                        or not routes.integer(marker.get("observed_tick"), state["started_tick"], memory.last_tick)
                        or active.goal != "rocket_launch" or len(active.steps) != 1
                        or active.steps[0].action not in {"factory_craft", "factory_extract"}):
                    raise ValueError("Solid funding and active kit plan disagree")
            if (not isinstance(memory.solid_epoch, dict)
                    or not isinstance(memory.solid_commitments, dict)
                    or len(memory.solid_commitments) > routes.MAX_ROUTES):
                raise ValueError("Invalid solid checkpoint binding")
            if not memory.solid_epoch:
                # A failed first observation has no authoritative native epoch.
                # Keep every base-memory lock/history field; refuse construction
                # on resume until an operator-owned reconciliation is available.
                if (memory.status != "uncertain" or memory.reason != UNBOUND_FAULT
                        or memory.solid_commitments):
                    raise ValueError("Invalid unbound solid checkpoint")
            elif (set(memory.solid_epoch) != {"actor_index", "surface_index", "force_index"}
                  or any(not routes.integer(v, 1) for v in memory.solid_epoch.values())):
                raise ValueError("Invalid solid checkpoint binding")
            units, receipts = set(), set()
            intents = {(i["source"], i["target"], i["item"], i["destination"]) for i in memory.solid_intents}
            for key, saved in memory.solid_commitments.items():
                routes.validate_commitment(saved, key)
                if (saved["source"]["role"], saved["target"]["role"], saved["item"], saved["target"]["inventory"]) not in intents:
                    raise ValueError("Checkpoint owns an unrequested route")
                ids = {saved["source"]["unit_number"], saved["target"]["unit_number"],
                       *[v["unit_number"] for v in saved["parts"].values()]}
                new_receipts = {v["receipt"] for v in saved["parts"].values()}
                if units & ids or receipts & new_receipts:
                    raise ValueError("Checkpoint double-owns a route component")
                units.update(ids); receipts.update(new_receipts)
            routes.validate_corridor_reservations(memory.solid_commitments)
            return memory

    return type("SolidRouteLoop", (SolidRouteMixin, base), {"memory_type": SolidMemory, "__module__": __name__})
