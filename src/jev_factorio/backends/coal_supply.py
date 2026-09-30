"""Sequential native attachment for the explicitly configured coal source contract."""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
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
        self._last_observation = None
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
        self._last_observation = snapshot
        return snapshot

    def economic_projection(self, snapshot, memory):
        return self._economic_projection(snapshot, memory, v6=False)

    def economic_projection_v6(self, snapshot, memory):
        """Use only the separately qualified v6 read-only native profile."""
        return self._economic_projection(snapshot, memory, v6=True)

    def economic_projection_v7(self, snapshot, memory):
        """Use the fixed v7 query; its native admission remains read-only here."""
        return self._economic_projection(snapshot, memory, v6=False, v7=True)

    def native_admission_journal_readback(self):
        """Read a one-use paid/ambiguous admission journal without replaying it."""
        if not self.coal_economic_admission:
            raise ValueError('Native admission readback requires the immutable treatment')
        snapshot = self._last_observation
        if snapshot is None:
            raise ValueError('Native admission readback requires a current campaign observation')
        runtime = snapshot.factory.get('acceptance_runtime')
        supply = snapshot.factory.get('coal_supply')
        if (not isinstance(runtime, dict) or runtime.get('session_id') != snapshot.session_id
                or type(runtime.get('actor_unit')) is not int
                or not isinstance(supply, dict)):
            raise ValueError('Native admission readback lacks a bound campaign observation')
        attachment = getattr(getattr(self.native, 'backend', None), '_native_attachment', None)
        manifest = attachment.get('native_installation') if isinstance(attachment, dict) else None
        from .native_attachment import (
            CLOSED_WORLD_PROFILE, MANUAL_CYCLE_PROFILE, require_asset,
        )
        if not isinstance(manifest, dict) or manifest.get('profile') not in {
                MANUAL_CYCLE_PROFILE, CLOSED_WORLD_PROFILE}:
            raise RuntimeError('Admission readback requires the retained v5/v6 native profile')
        require_asset(attachment, 'coal_manual_journal_v1')
        require_asset(attachment, 'coal_supply')
        if manifest['profile'] == CLOSED_WORLD_PROFILE:
            require_asset(attachment, 'coal_manual_cycle_v2')
        from ..coal_economic_v7 import (
            decode_admission_journal_readback,
            fixed_admission_journal_readback_query,
        )
        expected_epoch = {
            'session_id': snapshot.session_id,
            'tick': snapshot.tick,
            'actor_index': supply.get('actor_index'),
            'actor_unit': runtime['actor_unit'],
            'surface_index': supply.get('surface_index'),
            'force_index': supply.get('force_index'),
        }
        if any(type(expected_epoch[key]) is not int
               for key in ('tick', 'actor_index', 'actor_unit', 'surface_index', 'force_index')):
            raise ValueError('Native admission readback epoch is incomplete')
        source = fixed_admission_journal_readback_query()
        raw_response = self.native.command(source)
        raw = decode_native(raw_response)
        decoded = decode_admission_journal_readback(
            raw, expected_epoch=expected_epoch,
            expected_native_profile=manifest['profile'])
        return {
            'native': decoded,
            'raw_response': raw_response,
            'query_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
            'response_sha256': hashlib.sha256(raw_response.encode('utf-8')).hexdigest(),
        }

    def _economic_projection(self, snapshot, memory, *, v6, v7=False):
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
                raise RuntimeError('Closed-world census requires a qualified native installation')
            require_asset(attachment, 'coal_manual_cycle_v2')
            from ..coal_economic_v6 import decode_v6, fixed_query
            source = fixed_query()
            decoder = decode_v6
        elif v7:
            if (not isinstance(manifest, dict)
                    or manifest.get('profile') not in {MANUAL_CYCLE_PROFILE, CLOSED_WORLD_PROFILE}):
                raise RuntimeError('v7 census requires an exact qualified observer profile')
            require_asset(attachment, 'coal_manual_journal_v1')
            if manifest['profile'] == CLOSED_WORLD_PROFILE:
                require_asset(attachment, 'coal_manual_cycle_v2')
            from ..coal_economic_v7 import decode_v7, fixed_query
            source = fixed_query()
            decoder = decode_v7
        else:
            source = files("jev_factorio").joinpath("lua/coal_economics.lua").read_text()
            decoder = decode
        raw_response = self.native.command(source)
        raw = decode_native(raw_response)
        decode_epoch = expected_epoch
        if v7:
            raw_epoch = raw.get('epoch') if isinstance(raw, dict) else None
            epoch_fields = {'session_id', 'tick', 'actor_index', 'actor_unit',
                            'surface_index', 'force_index'}
            if (not isinstance(raw_epoch, dict) or set(raw_epoch) != epoch_fields
                    or any(type(raw_epoch[key]) is not int
                           for key in epoch_fields - {'session_id'} )
                    or type(raw_epoch['session_id']) is not str
                    or raw_epoch['session_id'] != snapshot.session_id
                    or raw_epoch['tick'] < snapshot.tick
                    or any(raw_epoch[key] != expected_epoch[key]
                           for key in ('actor_index', 'actor_unit',
                                       'surface_index', 'force_index'))):
                raise ValueError('Native v7 epoch differs from the current campaign binding')
            decode_epoch = raw_epoch
        decoded = decoder(raw, expected_epoch=decode_epoch,
                         expected_bundle={key: commitment(row) for key, row in rows.items()},
                         unit_qualification=UNIT_QUALIFICATION,
                         expected_connectors=connectors, expected_routes=expected_routes,
                         expected_journal_asset_sha256=journal_hash)
        return {"native": decoded, "raw_response": raw_response,
                "query_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "response_sha256": hashlib.sha256(raw_response.encode("utf-8")).hexdigest(),
                **({"coal_admission": decoded.native_admission} if v7 else {})}

    def _native_first_payment_required(self, parameters):
        if not self.coal_economic_admission or parameters.get('part') != 'chest':
            return False
        snapshot = self._last_observation
        if snapshot is None:
            return True
        data = snapshot.factory.get('coal_supply')
        if not isinstance(data, dict):
            return True
        raw_rows = data.get('sources')
        if not isinstance(raw_rows, dict):
            return True
        try:
            rows = coal.sources(snapshot)
        except (ValueError, KeyError, TypeError, AttributeError):
            return True
        return not any(row['parts'] for row in rows.values())

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
                if self._native_first_payment_required(parameters):
                    from ..coal_economic_v7 import fixed_first_spend_query
                    source = fixed_first_spend_query(parameters)
                    raw = self.native.command(source)
                    envelope = decode_native(raw)
                    snapshot = self._last_observation
                    runtime = (snapshot.factory.get('acceptance_runtime')
                               if snapshot is not None else None)
                    source_data = (snapshot.factory.get('coal_supply')
                                   if snapshot is not None else None)
                    if not isinstance(envelope, dict):
                        raise ValueError('Native first-payment response is not an object')
                    epoch = envelope.get('epoch')
                    epoch_fields = {'session_id', 'tick', 'actor_index', 'actor_unit',
                                    'surface_index', 'force_index'}
                    if (not isinstance(epoch, dict) or set(epoch) != epoch_fields
                            or any(type(epoch[key]) is not int
                                   for key in epoch_fields - {'session_id'})
                            or type(epoch['session_id']) is not str
                            or not isinstance(runtime, dict)
                            or not isinstance(source_data, dict)
                            or epoch['session_id'] != snapshot.session_id
                            or epoch['tick'] < snapshot.tick
                            or epoch['actor_index'] != source_data.get('actor_index')
                            or epoch['actor_unit'] != runtime.get('actor_unit')
                            or epoch['surface_index'] != source_data.get('surface_index')
                            or epoch['force_index'] != source_data.get('force_index')):
                        raise ValueError('Native first-payment epoch differs from its campaign')
                    from ..coal_economic_v7 import (
                        NATIVE_ADMISSION_SOURCE_SHA256, _native_admission,
                        NATIVE_COAL_SUPPLY_ASSET_SHA256,
                    )
                    admission = _native_admission(envelope.get('admission'), epoch,
                                                  NATIVE_ADMISSION_SOURCE_SHA256)
                    attempt = envelope.get('attempt')
                    if (not isinstance(envelope, dict)
                            or set(envelope) != {'schema', 'query_status', 'reason',
                                                 'epoch', 'admission', 'action', 'attempt'}
                            or envelope['schema'] != 'jev.coal-native-first-payment.v1'
                            or envelope['query_status'] != 'observed'
                            or admission.get('qualified') is not True
                            or not isinstance(attempt, dict)
                            or set(attempt) != {'schema', 'phase', 'session_id', 'actor_unit',
                                'target', 'layout', 'part', 'receipt', 'tick',
                                'source_asset_sha256', 'builder_asset_sha256',
                                'admission', 'paid'}
                            or attempt['schema'] != 'jev.native-coal-admission-attempt.v1'
                            or attempt['phase'] != 'paid'
                            or attempt['session_id'] != epoch['session_id']
                            or attempt['actor_unit'] != epoch['actor_unit']
                            or attempt['target'] != parameters['target']
                            or attempt['layout'] != parameters['layout']
                            or attempt['part'] != parameters['part']
                            or attempt['receipt'] != parameters['receipt']
                            or attempt['tick'] != epoch['tick']
                            or attempt['source_asset_sha256'] != NATIVE_ADMISSION_SOURCE_SHA256
                            or attempt['builder_asset_sha256'] != NATIVE_COAL_SUPPLY_ASSET_SHA256
                            or attempt['admission'] != admission
                            or not isinstance(attempt['paid'], dict)
                            or set(attempt['paid']) != {'role', 'unit_number', 'paid', 'tick'}
                            or attempt['paid']['role'] != coal.role(
                                parameters['target'], parameters['part'])
                            or type(attempt['paid']['unit_number']) is not int
                            or attempt['paid']['unit_number'] < 1
                            or attempt['paid']['paid'] != 1
                            or type(attempt['paid']['tick']) is not int
                            or attempt['paid']['tick'] < attempt['tick']
                            or attempt['paid']['tick'] > epoch['tick']
                            or not isinstance(envelope['action'], dict)
                            or envelope['action'].get('status') != 'placed'
                            or envelope['action'].get('result') != {'placed': True}):
                        raise ValueError('Native same-RPC coal admission rejected or unqualified')
                    result = {
                        'schema': 'jev.coal-native-first-payment-receipt.v1',
                        'query_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                        'response_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                        'source_asset_sha256': NATIVE_ADMISSION_SOURCE_SHA256,
                        'admission': admission,
                        'attempt': attempt,
                        'epoch': epoch,
                        'action': envelope['action'],
                    }
                    return json.dumps(result, sort_keys=True, separators=(',', ':'),
                                      ensure_ascii=True, allow_nan=False)
                self.native.call("build_coal_source", parameters)
        return "Coal placement returned; exact paid ownership requires fresh observation"
