"""Bounded, content-free observation profiling; metrics never authorize actions."""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path

MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
LABELS = {"fair_state", "player_state", "inventory", "entities", "output_inventory",
          "native_factory", "campaign_snapshot", "discovery", "nearest", "other"}


def label(command: str) -> str:
    # Fixed labels only: never retain Lua, targets, URLs, native errors or secrets.
    for needle, name in (("campaign.observation_snapshot", "campaign_snapshot"),
                         ("campaign.observe()", "campaign_snapshot"),
                         ("discover_mine_target", "discovery"),
                         ("next_mine_target", "discovery"),
                         ("fair.observe", "fair_state"), ("inspect_inventory", "inventory"),
                         ("get_entities", "entities"), ("nearest", "nearest"),
                         ("position={agent.position", "player_state")):
        if needle in command:
            return name
    return "other"


class ObservationProfile:
    """Ordered, nested wall/process-CPU clocks; never a network-time estimator.

    A profile belongs to one sequential observation. Process CPU is for this
    Python process, including other threads, not native Factorio or host CPU.
    Inclusive helper/RPC durations remain for compatibility. Exclusive nested
    intervals partition each observed clock without adding parents to children.
    """
    def __init__(self, clock=time.perf_counter_ns, cpu_clock=time.process_time_ns):
        self.clock, self.cpu_clock = clock, cpu_clock
        self.started, self.cpu_started = clock(), cpu_clock()
        self.calls: dict[str, dict] = {}
        self.native_ns: dict[str, int] = {}
        self.subcalls: dict[str, dict] = {}
        self.decode_ns = 0
        self.cache = Counter()
        self._stack: list[dict] = []
        self._exclusive = Counter()
        self._exclusive_cpu = Counter()

    @contextmanager
    def _timed(self, category, name):
        start, cpu_start = self.clock(), self.cpu_clock()
        frame = {'children': 0, 'child_cpu': 0, 'failed': False,
                 'response_bytes': 0, 'request_bytes': 0}
        self._stack.append(frame)
        try:
            yield frame
        except BaseException:
            frame['failed'] = True
            raise
        finally:
            elapsed = max(0, self.clock() - start)
            cpu = max(0, self.cpu_clock() - cpu_start)
            self._stack.pop()
            exclusive = max(0, elapsed - frame['children'])
            exclusive_cpu = max(0, cpu - frame['child_cpu'])
            self._exclusive[category] += exclusive
            self._exclusive_cpu[category] += exclusive_cpu
            if self._stack:
                self._stack[-1]['children'] += elapsed
                self._stack[-1]['child_cpu'] += cpu
            if category == 'decode':
                self.decode_ns += elapsed
            else:
                destination = self.calls if category == 'rpc' else self.subcalls
                row = destination.setdefault(name, {
                    'count': 0, 'failed': 0, 'total_ns': 0, 'max_ns': 0,
                    'exclusive_ns': 0, 'process_cpu_ns': 0, 'exclusive_process_cpu_ns': 0})
                for key, value in (('count', 1), ('failed', int(frame['failed'])),
                                   ('total_ns', elapsed), ('exclusive_ns', exclusive),
                                   ('process_cpu_ns', cpu), ('exclusive_process_cpu_ns', exclusive_cpu)):
                    row[key] += value
                row['max_ns'] = max(row['max_ns'], elapsed)
                if category == 'rpc':
                    for key in ('request_bytes', 'response_bytes'):
                        row[key] = row.get(key, 0) + frame[key]

    def rpc(self, name, operation, request_bytes=0):
        name = name if name in LABELS else 'other'
        with self._timed('rpc', name) as frame:
            frame['request_bytes'] = request_bytes
            result = operation()
            values = result.values() if isinstance(result, dict) else [result]
            frame['response_bytes'] = sum(len(v.encode('utf-8')) for v in values if isinstance(v, str))
            return result

    def subcall(self, name, operation):
        name = name if name in LABELS else 'other'
        with self._timed('helpers', name):
            return operation()

    def decode(self, raw):
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_PAYLOAD_BYTES:
            raise ValueError('Observation payload exceeds the declared budget')
        with self._timed('decode', 'other'):
            return json.loads(raw)

    def summary(self):
        total = max(0, self.clock() - self.started)
        cpu = max(0, self.cpu_clock() - self.cpu_started)
        rpc = sum(row['total_ns'] for row in self.calls.values())
        # Only completed, ordered intervals may be partitioned. A mid-call
        # diagnostic remains explicitly unavailable rather than inventing time.
        complete = not self._stack
        wall = {name: self._exclusive[name] for name in ('rpc', 'helpers', 'decode')}
        cpu_parts = {name: self._exclusive_cpu[name] for name in ('rpc', 'helpers', 'decode')}
        complete = complete and sum(wall.values()) <= total and sum(cpu_parts.values()) <= cpu
        wall['unattributed'] = total - sum(wall.values()) if complete else None
        cpu_parts['unattributed'] = cpu - sum(cpu_parts.values()) if complete else None
        legacy_residual = max(0, total - rpc - self.decode_ns)
        return {'schema': 1, 'attribution_schema': 1, 'total_ns': total,
                'process_cpu_ns': cpu, 'wall_clock': 'perf_counter_ns',
                'cpu_clock': 'process_time_ns', 'cpu_scope': 'python_process_including_other_threads',
                'calls': deepcopy(self.calls), 'subcalls': deepcopy(self.subcalls),
                'decode_ns': self.decode_ns, 'native_ns': dict(self.native_ns),
                'decode_scope': 'instrumented_envelopes_only',
                'local_unattributed_ns': legacy_residual,
                'unattributed_wall_ns': wall['unattributed'],
                'legacy_residual_scope': 'wall_minus_inclusive_rpc_and_envelope_decode_not_cpu',
                'wall_partition_ns': wall if complete else None,
                'process_cpu_partition_ns': cpu_parts if complete else None,
                'partition_complete': complete,
                'cache': dict(self.cache), 'durations_are_inclusive': True,
                'native_timing_available': bool(self.native_ns),
                'rpc_includes_native_and_transport': True,
                'transport_separation_available': False,
                'helper_retry_attempts': None, 'helper_backoff_ns': None}


class ProfiledTools:
    """Time opaque FLE helpers even when they retain their own RCON reference."""
    def __init__(self, tools, profile):
        self.tools, self.profile = tools, profile

    def __getattr__(self, name):
        original = getattr(self.tools, name)
        labels = {"inspect_inventory": "inventory", "get_entities": "entities", "nearest": "nearest"}
        if name not in labels or not callable(original):
            return original
        def invoke(*args, **kwargs):
            key = "output_inventory" if name == "inspect_inventory" and (args or kwargs) else labels[name]
            return self.profile.subcall(key, lambda: original(*args, **kwargs))
        return invoke


class ProfiledRcon:
    def __init__(self, client, profile):
        self.client, self.profile = client, profile

    def __getattr__(self, name):
        return getattr(self.client, name)

    def send_command(self, command):
        return self.profile.rpc(label(command), lambda: self.client.send_command(command),
                                len(command.encode("utf-8")))

    def send_commands(self, commands):
        # Preserve the delegate's batching and result keys exactly.
        return self.profile.rpc("other", lambda: self.client.send_commands(commands),
                                sum(len(v.encode("utf-8")) for v in commands.values()))


@contextmanager
def profile_backend(backend):
    profile = ObservationProfile()
    original = backend._instance.rcon_client
    backend._instance.rcon_client = ProfiledRcon(original, profile)
    backend._observation_profile = profile
    try:
        yield profile
    finally:
        backend._instance.rcon_client = original
        backend._observation_profile = None
        backend.last_observation_profile = profile.summary()


def parse_snapshot(raw: str, profile: ObservationProfile, *, schemas: tuple[int, ...] = (1,)) -> dict:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Invalid bounded native observation")
    payloads = []
    for line in raw.splitlines():
        if line.startswith("JEV_SNAPSHOT|"):
            payloads.append(line.removeprefix("JEV_SNAPSHOT|"))
        elif line.startswith("JEV_NATIVE_PROFILE|"):
            match = re.fullmatch(r"JEV_NATIVE_PROFILE\|(campaign_snapshot|discovery|serialize)\|\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|us|s)", line)
            if match:
                name, amount, unit = match.groups()
                value = float(amount) * {"s": 1e9, "ms": 1e6, "us": 1e3}[unit]
                if math.isfinite(value) and 0 <= value <= 1e15:
                    profile.native_ns[name] = round(value)
            # Unrecognized localized profiler output is unknown, never zero.
    if len(payloads) != 1:
        raise ValueError("Missing or ambiguous native observation envelope")
    result = profile.decode(payloads[0])
    if not isinstance(result, dict) or type(result.get("schema")) is not int or result["schema"] not in schemas:
        raise ValueError("Unsupported native observation envelope")
    return result


def host_pressure(root: Path = Path("/proc")) -> dict:
    """Read only fixed non-identifying host counters, never environment or argv."""
    result = {"schema": 1, "sampled_monotonic_ns": time.monotonic_ns(), "available": False}
    try:
        memory = {}
        for line in (root / "meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in {"MemAvailable", "MemTotal", "SwapTotal", "SwapFree"}:
                memory[key + "_kib"] = int(value.split()[0])
        pressure = {}
        for kind in ("cpu", "memory", "io"):
            path = root / "pressure" / kind
            if path.is_file():
                rows = {}
                for line in path.read_text().splitlines():
                    parts = line.split()
                    if parts and parts[0] in {"some", "full"}:
                        values = dict(part.split("=", 1) for part in parts[1:])
                        row = {key: float(values[key]) for key in ("avg10", "avg60", "avg300", "total") if key in values}
                        if all(math.isfinite(v) and v >= 0 for v in row.values()):
                            rows[parts[0]] = row
                pressure[kind] = rows
        result.update(available=True, memory=memory, pressure=pressure)
    except (OSError, ValueError, IndexError):
        result["reason"] = "host_counters_unavailable"
    return result
