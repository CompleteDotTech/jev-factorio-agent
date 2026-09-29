"""Sequential native attachment for the explicitly configured coal source contract."""
from __future__ import annotations

import hashlib
from importlib.resources import files
from types import SimpleNamespace

from .. import coal_supply as coal
from ..iteration_timing import decode_native
from ..solid_routes import point
from ..telemetry import Trace, phase
from .solid_routes import SolidRouteFactory


class CoalSupplyFactory:
    """No game startup, CLI opt-in, provider call or automatic runtime migration."""

    def __init__(self, native, targets, *, coal_economic_admission: bool = False) -> None:
        if type(coal_economic_admission) is not bool:
            raise ValueError("Coal economic admission must be an explicit boolean")
        self.coal_economic_admission = coal_economic_admission
        self.targets = coal.validate_targets(targets)
        current = native
        while current is not None and not isinstance(current, SolidRouteFactory):
            current = getattr(current, "native", None)
        if current is None:
            raise ValueError("Coal supply requires an explicitly bound solid-route attachment")
        coal.validate_transport_intents(self.targets, current.intents)
        self.native = native
        if getattr(native.backend, '_native_attachment', None) is not None:
            from .native_attachment import require_asset
            require_asset(native.backend._native_attachment, 'coal_supply')
            retained = native.backend._native_attachment
            if (retained['coal_targets'] != self.targets
                    or retained['coal_admission_evidence'] is not coal_economic_admission):
                raise RuntimeError('Retained native coal treatment differs from requested targets')
        else:
            native.command(files("jev_factorio").joinpath("lua/coal_supply.lua").read_text())
            native.call("set_coal_targets", self.targets)
            native.call("set_coal_admission_evidence", coal_economic_admission)

    def __getattr__(self, name):
        return getattr(self.native, name)

    def observe(self, snapshot):
        snapshot = self.native.observe(snapshot)
        data = snapshot.factory.get("coal_supply")
        if isinstance(data, dict):
            if data.get("sources") == []:
                data["sources"] = {}
            if isinstance(data.get("sources"), dict):
                for row in data["sources"].values():
                    if isinstance(row, dict):
                        for key in ("parts", "flow", "pending", "manual_pending"):
                            if row.get(key) == []:
                                row[key] = {}
        return snapshot

    def economic_projection(self, snapshot, memory):
        return self._economic_projection(snapshot, memory, v6=False)

    def economic_projection_v6(self, snapshot, memory):
        """Use only the separately qualified v6 read-only native profile."""
        return self._economic_projection(snapshot, memory, v6=True)

    def _economic_projection(self, snapshot, memory, *, v6):
        """Query and decode a fresh native graph without granting admission.

        The caller must retain the returned raw response and query digest in a
        private receipt before relying on this projection. A snapshot field or
        a caller-supplied digest is never accepted as the
        source of native economic facts.
        """
        if not self.coal_economic_admission:
            raise ValueError("Coal economic projection requires the immutable treatment")
        from ..coal_economic_observation import UNIT_QUALIFICATION, decode
        from ..coal_supply import commitment, sources
        from ..connector_checkpoint import validate_binding
        from ..memory import CampaignMemory
        from .native_attachment import (
            CLOSED_WORLD_PROFILE, MANUAL_CYCLE_PROFILE, require_asset,
        )

        rows = sources(snapshot)
        runtime = snapshot.factory.get("acceptance_runtime")
        data = snapshot.factory["coal_supply"]
        if (not isinstance(runtime, dict) or runtime.get("session_id") != snapshot.session_id
                or type(runtime.get("actor_unit")) is not int or runtime["actor_unit"] < 1
                or data["protocol"] != 2 or data["admission"]["qualified"] is not False
                or set(rows) != set(self.targets) or data["committed"]):
            raise ValueError("Native coal economic projection lacks a current unpaid owner")
        if not isinstance(memory, CampaignMemory) or memory.session_id != snapshot.session_id:
            raise ValueError('Coal economic projection requires the session checkpoint')
        validated = validate_binding(memory.connector_ownership, snapshot.session_id)
        observed = snapshot.factory.get('connector_ownership')
        if (not isinstance(observed, dict) or observed.get('protocol') != 1
                or observed.get('session_id') != snapshot.session_id
                or observed.get('tick') != snapshot.tick or observed.get('active') is not None):
            raise ValueError('Coal economic connector checkpoint is not current')
        observed_routes = observed.get('routes')
        if observed_routes == []:
            observed_routes = {}
        if not isinstance(observed_routes, dict) or set(observed_routes) != set(validated['routes']):
            raise ValueError('Coal economic connector route set changed')
        connectors = {}
        expected_routes = {}
        for receipt, route in validated['routes'].items():
            if route['state'] != 'complete' or route['owned'] is not True:
                raise ValueError('Coal economic graph has unresolved connector work')
            if (route['actor_unit'] != runtime['actor_unit']
                    or route['surface_index'] != data['surface_index']
                    or route['force_index'] != data['force_index']):
                raise ValueError('Coal economic connector epoch changed')
            summary = observed_routes[receipt]
            identity = ('id', 'source', 'target', 'source_unit', 'target_unit', 'kind',
                        'fluid', 'actor_unit', 'session_id', 'surface_index', 'force_index',
                        'state', 'owned', 'paid', 'external', 'pending')
            if (not isinstance(summary, dict)
                    or any(summary.get(key) != route[key] for key in identity)
                    or summary.get('cell_count') != len(route['cells'])):
                raise ValueError('Coal economic connector summary differs from checkpoint')
            expected_routes[receipt] = {key: route[key] for key in identity
                                        if key not in ('external', 'pending')}
            expected_routes[receipt]['cell_count'] = len(route['cells'])
            for cell in route['cells']:
                role = f"connector:{receipt}:{cell['index']}"
                connectors[role] = {'unit': cell['unit_number'], 'name': route['kind'],
                                    'position': cell['position']}
                if len(connectors) > 128:
                    raise ValueError('Coal economic connector graph exceeds bound')
        expected_epoch = {"session_id": snapshot.session_id, "tick": snapshot.tick,
                          "actor_index": data["actor_index"],
                          "actor_unit": runtime["actor_unit"],
                          "surface_index": data["surface_index"],
                          "force_index": data["force_index"]}
        journal_hash = None
        attachment = getattr(getattr(self.native, 'backend', None), '_native_attachment', None)
        manifest = None
        if isinstance(attachment, dict) and isinstance(attachment.get('native_installation'), dict):
            manifest = attachment['native_installation']
            if manifest.get('profile') in {MANUAL_CYCLE_PROFILE, CLOSED_WORLD_PROFILE}:
                require_asset(attachment, 'coal_manual_journal_v1')
                journal_hash = manifest['assets']['coal_manual_journal_v1']
        if v6:
            if not isinstance(manifest, dict) or manifest.get('profile') != CLOSED_WORLD_PROFILE:
                raise RuntimeError('V6 census requires a qualified native installation')
            require_asset(attachment, 'coal_manual_cycle_v2')
            from ..coal_economic_v6 import decode_v6, fixed_query
            source = fixed_query()
            decoder = decode_v6
        else:
            source = files("jev_factorio").joinpath("lua/coal_economics.lua").read_text()
            decoder = decode
        raw_response = self.native.command(source)
        decoded = decoder(decode_native(raw_response), expected_epoch=expected_epoch,
                         expected_bundle={key: commitment(row) for key, row in rows.items()},
                         unit_qualification=UNIT_QUALIFICATION,
                         expected_connectors=connectors, expected_routes=expected_routes,
                         expected_journal_asset_sha256=journal_hash)
        return {"native": decoded, "raw_response": raw_response,
                "query_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "response_sha256": hashlib.sha256(raw_response.encode("utf-8")).hexdigest()}

    def execute(self, action: str, parameters: dict, *, trace: Trace | None = None) -> str:
        if action != coal.COMMAND:
            return self.native.execute(action, parameters, **({"trace": trace} if trace is not None else {}))
        coal.validate(parameters)
        with phase("entity_lookup", trace):
            target = decode_native(self.native.call("prepare_coal_source", parameters))
        if not isinstance(target, dict) or target.get("name") != coal.PARTS[parameters["part"]]:
            raise ValueError("Native coal source returned the wrong placement prototype")
        point(target.get("position"))
        if target.get("already_paid") is not True:
            with phase("approach", trace):
                self.native.backend._fair.approach(SimpleNamespace(**target["position"]), target["name"])
            with phase("transfer_rpc", trace):
                self.native.call("build_coal_source", parameters)
        return "Coal placement returned; exact paid ownership requires fresh observation"
