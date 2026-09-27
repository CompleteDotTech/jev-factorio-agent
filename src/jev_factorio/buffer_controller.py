"""Compose output buffers with either foreground or acknowledged-background control."""
from __future__ import annotations

from copy import deepcopy

from .output_buffers import permits, sources
from .planning.output_buffers import OutputBufferPlanner
from .skills import Plan, Step


class OutputBufferMixin:
    planner_type = OutputBufferPlanner

    def __init__(self, backend, jev=None, **options) -> None:
        if options.get("factory_scheduling") != "ready-work":
            raise ValueError("Output buffers require ready-work scheduling")
        self._buffer_fault = False
        self._buffer_save_poisoned = False
        self._buffer_evidence = {}
        super().__init__(backend, jev, **options)
        if self.catalog is None:
            raise ValueError("Output buffers require a native production catalog")
        native = getattr(backend, "_factory", None)
        if native is not None:
            from .backends import has_adapter
            from .backends.output_buffers import OutputBufferFactory

            if not has_adapter(native, OutputBufferFactory):
                backend._factory = OutputBufferFactory(native)
        elif getattr(backend, "output_buffers_supported", False) is not True:
            raise ValueError("Backend does not support output-buffer evidence")

    def _save(self) -> None:
        if self._buffer_save_poisoned:
            raise RuntimeError("Buffer checkpoint persistence failed; reconstruct before continuing")
        try:
            super()._save()
        except BaseException:
            self._buffer_save_poisoned = True
            raise

    def _observe(self, stage="observe"):
        if self._buffer_save_poisoned:
            raise RuntimeError("Buffer checkpoint persistence failed; reconstruct before continuing")
        snapshot = super()._observe(stage)
        try:
            rows = sources(snapshot)
            if any(row.get("state") == "fault" for row in rows.values()):
                raise ValueError("Output-buffer identity, topology, or conservation requires reconciliation")
        except (ValueError, AttributeError):
            self._buffer_fault = True
            self.memory.status = "uncertain"
            self.memory.reason = "Output-buffer evidence invalid; preserve pending work for reconciliation"
            self._save()
        self._buffer_evidence = deepcopy(snapshot.factory.get("output_buffers", {}))
        return snapshot

    def _execution_barrier(self, snapshot) -> bool:
        return self._buffer_fault or self._buffer_save_poisoned or super()._execution_barrier(snapshot)

    def _step_allowed(self, step, snapshot) -> bool:
        return (not self._execution_barrier(snapshot)
                and permits(step.action, step.parameters or {}, snapshot)
                and super()._step_allowed(step, snapshot))

    def _verify_pending(self, snapshot):
        if self._execution_barrier(snapshot):
            return self._record(snapshot, "observe", self.memory.reason)
        return super()._verify_pending(snapshot)

    def _record_extras(self) -> dict:
        return {**super()._record_extras(), "furnace_output_buffers": True,
                "buffer_evidence": deepcopy(self._buffer_evidence)}

    def _compile_candidates(self, snapshot):
        # The base compiler now uses the effective planner_type exactly once.
        # Keep background work/prefetch in the parent chain, then apply the
        # ownership/lock barriers without recompiling or inventing a fallback.
        plans, blocker = super()._compile_candidates(snapshot)
        if self.memory.active_goal == "bootstrap_mining":
            return plans, blocker
        selected = [plan for plan in plans if self._step_allowed(plan.steps[0], snapshot)]
        if selected:
            return selected, blocker
        job = getattr(self, "_job", lambda: None)()
        if job is not None or snapshot.factory.get("crafting_queue", 0):
            return [Plan("buffer:crafting-wait", self.memory.active_goal,
                         "No independent buffer-safe work; observe native crafting",
                         (Step("factory_wait", "crafting_idle", timeout_ticks=1800),))], ""
        return [], blocker or "No buffer-safe production action"


def buffered_loop_type(base):
    """Compose with an explicitly selected controller, without changing defaults."""
    return type("OutputBufferLoop", (OutputBufferMixin, base), {"__module__": __name__})
