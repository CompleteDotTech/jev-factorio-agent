"""Reproducible API-shaped fixture; never launches Factorio or a model provider."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from test_solid_kit_acquisition import kit_loop
from solid_routes_fixtures import ROUTE, row
from jev_factorio import solid_routes


def run_fixture() -> dict:
    with TemporaryDirectory(prefix='solid-kit-fixture-') as folder:
        loop, backend = kit_loop(Path(folder))
        before = dict(backend.state.inventory)
        with redirect_stdout(StringIO()):
            for _ in range(20):
                result = loop.step()
                if not result['verified']:
                    raise RuntimeError('Fixture failed to verify its next paid step')
                if row(backend.state)['state'] == 'ready':
                    break
            loop._observe()
        route = row(backend.state)
        if route['state'] != 'ready' or len(route['parts']) != len(route['steps']):
            raise RuntimeError('Fixture did not construct its bounded corridor')
        return {'schema': 1, 'evidence_kind': 'deterministic_fixture',
                'native_acceptance': False, 'deployed': False,
                'inventory_before': before, 'inventory_after': dict(backend.state.inventory),
                'actions': [action for action, _ in backend.calls],
                'paid_components': len(route['parts']),
                'construction_actions': sum(action == solid_routes.COMMAND for action, _ in backend.calls),
                'funding_released': loop.memory.solid_funding is None,
                'paid_commitment_retained': ROUTE in loop.memory.solid_commitments,
                'flow_proven': solid_routes.flow_complete(ROUTE, route['layout'], backend.state),
                'failures': dict(loop.memory.failures),
                'limits': ['API-shaped fixture, not Factorio physics',
                           'No native science, latency, resource or paid-coal proof']}


if __name__ == '__main__':
    print(json.dumps(run_fixture(), indent=2, sort_keys=True, allow_nan=False))
