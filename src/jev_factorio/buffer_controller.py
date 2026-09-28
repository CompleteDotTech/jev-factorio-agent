"""Compose output buffers with either foreground or acknowledged-background control."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path

from .output_buffers import (COMMAND, PARTS, SOURCES, current, expected_commitments,
                             permits, sources, validate_commitments)
from .planning.output_buffers import OutputBufferPlanner
from .skills import Plan, Step


class OutputBufferMixin:
    planner_type = OutputBufferPlanner

    def __init__(self, backend, jev=None, **options) -> None:
        if options.get("factory_scheduling") != "ready-work":
            raise ValueError("Output buffers require ready-work scheduling")
        if options.get("resume_controller"):
            # Reject unsafe legacy enablement before enable_factory or adapters
            # can initialize native state. Loading does not rewrite these bytes.
            path = Path(options.get("checkpoint") or "")
            raw = path.read_bytes()
            saved = json.loads(raw)
            if not isinstance(saved, dict):
                raise ValueError('Invalid output-buffer checkpoint')
            self.memory_type.from_bytes(raw, saved.get("session_id"), options.get("target", "rocket_launch"))
            if path.read_bytes() != raw:
                raise ValueError("Checkpoint changed during output-buffer validation")
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
        changed = False
        try:
            rows = sources(snapshot)
            expected = expected_commitments(self.memory)
            retained = deepcopy(self.memory.output_commitments)
            observed = deepcopy(expected)
            for source, old in expected.items():
                row = rows.get(source)
                if (not isinstance(row, dict) or row.get("source_unit") != old["source_unit"]
                        or row.get("layout") != old["layout"]
                        or any(row.get("parts", {}).get(part) != paid for part, paid in old["parts"].items())):
                    raise ValueError("Output-buffer ownership disappeared or changed")
            plan = self.memory.active_plan or {}
            steps = plan.get("steps", [])
            pending, attempt = self.memory.pending or {}, self.memory.attempt or {}
            step = steps[self.memory.step_index] if pending and self.memory.step_index < len(steps) else {}
            parameters = step.get("parameters") or {}
            for source, row in rows.items():
                if not current(row, snapshot) or row.get("source") != source:
                    raise ValueError("Output-buffer identity, topology, or conservation requires reconciliation")
                source_entity = snapshot.factory.get('entities', {}).get(source, {})
                if type(source_entity.get('unit_number')) is not int:
                    raise ValueError('Invalid output-buffer source identity')
                candidate = {"layout": row.get("layout"), "source_unit": row.get("source_unit"),
                             "parts": deepcopy(row.get("parts"))}
                validate_commitments({source: candidate}, successors=hasattr(self.memory, 'successor_schema'))
                before = expected.get(source, {}).get("parts", {})
                new = set(candidate['parts']) - set(before)
                if new and (len(new) != 1 or pending.get('action') != COMMAND
                        or pending.get('dispatch') not in {'prepared', 'ambiguous', 'returned'}
                        or step.get('action') != COMMAND or attempt.get('action') != COMMAND
                        or parameters.get('source') != source or parameters.get('layout') != candidate['layout']
                        or parameters.get('part') not in new
                        or candidate['parts'][parameters['part']]['receipt'] != parameters.get('receipt')
                        or attempt.get('receipt') != parameters.get('receipt')):
                    raise ValueError('Untracked output-buffer construction')
                for part, paid in candidate['parts'].items():
                    entity = snapshot.factory.get('entities', {}).get(paid['role'], {})
                    if (type(entity.get('unit_number')) is not int or entity['unit_number'] != paid['unit_number']
                            or entity.get('name') != PARTS[part]):
                        raise ValueError('Output-buffer paid entity missing or replaced')
                # The outer successor observer remains the sole writer for new
                # growth output receipts. Duplicating an evolving paid prefix
                # here would save it before that observer validates its update.
                if (source in SOURCES and candidate['parts']) or source in retained:
                    retained[source] = candidate
                observed[source] = candidate
            validate_commitments(observed, successors=hasattr(self.memory, 'successor_schema'))
            validate_commitments(retained)
            # No partial ownership update survives a later row failing validation.
            changed = retained != self.memory.output_commitments
            if changed:
                self.memory.output_commitments = retained
        except (ValueError, KeyError, TypeError, AttributeError):
            self._buffer_fault = True
            self.memory.status = "uncertain"
            self.memory.reason = "Output-buffer evidence invalid; preserve pending work for reconciliation"
        self._buffer_evidence = deepcopy(snapshot.factory.get("output_buffers", {}))
        # Dynamic dispatch preserves the outer SolidMixin first-resume transaction.
        if changed or self._buffer_fault:
            self._save()
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
    """Explicit durable extension; legacy inspectors never invent paid owners."""
    @dataclass
    class OutputMemory(base.memory_type):
        output_buffers_schema: int = 1
        output_commitments: dict = field(default_factory=dict)

        @classmethod
        def _from_data(cls, data, session_id, target):
            if not isinstance(data, dict):
                raise ValueError('Invalid output-buffer checkpoint')
            keys = {'output_buffers_schema', 'output_commitments'}
            legacy = not keys.intersection(data)
            if not legacy and not keys <= data.keys():
                raise ValueError('Incomplete output-buffer checkpoint extension')
            memory = super()._from_data(data, session_id, target)
            if legacy:
                if (memory.status != 'running' or any(getattr(memory, key, None) for key in (
                        'active_plan', 'pending', 'attempt', 'reservations', 'background_job',
                        'background_attempt', 'capital_investment', 'solid_funding', 'coal_funding',
                        'transfer_recovery'))
                        or any(project.get('status') != 'qualified'
                               for project in getattr(memory, 'successor_projects', {}).values())):
                    raise ValueError('Enable output ownership only at an idle, reconciled boundary')
                memory.event('output_ownership_enabled', tick=memory.last_tick,
                             reason='explicit_empty_ownership_at_idle_boundary')
            if type(memory.output_buffers_schema) is not int or memory.output_buffers_schema != 1:
                raise ValueError('Invalid output-buffer checkpoint schema')
            validate_commitments(memory.output_commitments)
            expected_commitments(memory)  # Distinct owner families cannot alias.
            return memory

    return type("OutputBufferLoop", (OutputBufferMixin, base),
                {"memory_type": OutputMemory, "__module__": __name__})
