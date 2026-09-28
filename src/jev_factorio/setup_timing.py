"""Optional, content-free boundaries for one-use controller initialization.

The diagnostic is written after the controller exits.  It never authorizes a
native action and a failed diagnostic cannot change a controller result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time

from .iteration_timing import validate_timing


STAGES = (
    'setup_start', 'research_ready', 'dashboard_ready', 'preflight_ready',
    'backend_ready', 'controller_ready', 'outputs_ready', 'initialized',
)
BACKEND_STAGES = ('attach_start', 'instance_ready', 'installation_ready', 'fair_ready')


class SetupTiming:
    def __init__(self, path: Path, *, backend_expected=False, wall_clock=None, cpu_clock=None):
        self.path = path
        self.backend_expected = backend_expected
        self.wall_clock = wall_clock or time.perf_counter_ns
        self.cpu_clock = cpu_clock or time.process_time_ns
        self.cuts: list[tuple[str, int, int]] = []
        self.backend_cuts: list[tuple[str, int, int]] = []
        self.valid = True
        self.final_iteration = None

    def mark(self, stage: str) -> None:
        if stage not in STAGES or (self.cuts and STAGES.index(stage) <= STAGES.index(self.cuts[-1][0])):
            self.valid = False
            return
        try:
            wall, cpu = self.wall_clock(), self.cpu_clock()
        except Exception:
            self.valid = False
            return
        if type(wall) is not int or type(cpu) is not int or wall < 0 or cpu < 0:
            self.valid = False
            return
        if self.cuts and (wall < self.cuts[-1][1] or cpu < self.cuts[-1][2]):
            self.valid = False
            return
        self.cuts.append((stage, wall, cpu))

    def mark_backend(self, stage: str) -> None:
        if stage not in BACKEND_STAGES or (self.backend_cuts and
                BACKEND_STAGES.index(stage) <= BACKEND_STAGES.index(self.backend_cuts[-1][0])):
            self.valid = False
            return
        try:
            wall, cpu = self.wall_clock(), self.cpu_clock()
        except Exception:
            self.valid = False
            return
        if (type(wall) is not int or type(cpu) is not int or wall < 0 or cpu < 0 or
                self.backend_cuts and (wall < self.backend_cuts[-1][1] or cpu < self.backend_cuts[-1][2])):
            self.valid = False
            return
        self.backend_cuts.append((stage, wall, cpu))

    def result(self) -> dict:
        phases = []
        for (start, wall0, cpu0), (end, wall1, cpu1) in zip(self.cuts, self.cuts[1:]):
            phases.append({'from': start, 'to': end,
                           'wall_ns': wall1 - wall0, 'process_cpu_ns': cpu1 - cpu0})
        backend_phases = [
            {'from': start, 'to': end, 'wall_ns': wall1 - wall0,
             'process_cpu_ns': cpu1 - cpu0}
            for (start, wall0, cpu0), (end, wall1, cpu1) in
            zip(self.backend_cuts, self.backend_cuts[1:])
        ]
        backend_complete = ([row[0] for row in self.backend_cuts] == list(BACKEND_STAGES)
                            if self.backend_expected else not self.backend_cuts)
        return {'schema': 'jev.setup-timing.v1',
                'status': ('complete' if self.valid and
                           [row[0] for row in self.cuts] == list(STAGES) and
                           backend_complete else 'partial'),
                'scope': 'ordered_one_use_setup_boundaries_no_native_or_wire_attribution',
                'phases': phases, 'backend_phases': backend_phases,
                'final_iteration': self.final_iteration}

    def capture_final_iteration(self, loop) -> None:
        """Publish the completed final step, leaving its following gap unknown."""
        pending = getattr(loop, '_timing_pending', None)
        summary = pending.get('summary') if isinstance(pending, dict) else None
        if not isinstance(summary, dict):
            return
        candidate = {**summary, 'gap': {
            'complete': False, 'total_ns': None, 'intentional_sleep_ns': None,
            'other_gap_ns': None,
            'sleep_calls': pending.get('sleep_calls', 0),
            'sleep_failed': pending.get('sleep_failed', 0),
            'requested_sleep_ns': pending.get('requested_sleep_ns', 0),
            'scope': 'previous_decorated_step_end_to_current_decorated_step_start',
        }}
        try:
            validate_timing(candidate)
        except Exception:
            self.valid = False
        else:
            self.final_iteration = candidate

    def capture_final_iteration_safely(self, loop) -> None:
        try:
            self.capture_final_iteration(loop)
        except BaseException:
            self.valid = False

    def write(self) -> None:
        """Best-effort exclusive publication; never replace an existing result."""
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.path.parent, prefix='.setup-timing-',
                                             delete=False) as stream:
                tmp = Path(stream.name)
                os.chmod(tmp, 0o600)
                json.dump(self.result(), stream, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(tmp, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except Exception:
            # This is diagnostic only. The owner checks for a missing result.
            pass
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
