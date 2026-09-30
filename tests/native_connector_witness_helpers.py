"""Synthetic durable snapshot witness fixtures for native readback tests."""
import hashlib
import json
from pathlib import Path

from jev_factorio.backends.native_attachment import (
    CONNECTOR_OBSERVER_WITNESS_NAME, PINNED_ASSETS,
    PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE,
    connector_observer_bridge_sha256, connector_snapshot_sha256,
    connector_snapshot_command,
)


def qualify_snapshot_row(row, *, tick=900):
    snapshot = {'protocol': 1, 'session_id': row['session_id'], 'tick': tick,
                'active': None, 'routes': {}}
    row['connector_snapshot_qualified'] = True
    row['connector_snapshot_tick'] = tick
    row['connector_snapshot_ownership'] = snapshot
    return snapshot


def write_snapshot_witness(directory, row, *, receipt_path=None, snapshot_mode="coherent"):
    directory = Path(directory)
    receipt = Path(receipt_path) if receipt_path is not None else directory / 'attachment.json'
    if receipt_path is None:
        receipt_payload = {
            'schema': 'jev.native-attachment.v1', 'session_id': row['session_id'],
            'actor_unit': row['actor_unit'], 'installed_source_commit': PINNED_SOURCE_COMMIT,
            'installed_source_tree': PINNED_SOURCE_TREE, 'installed_assets': PINNED_ASSETS,
        }
        receipt_bytes = json.dumps(receipt_payload, sort_keys=True).encode()
        receipt.write_bytes(receipt_bytes)
        receipt.chmod(0o600)
    else:
        receipt_bytes = receipt.read_bytes()
    snapshot = qualify_snapshot_row(row)
    rows = [
        {'schema': 'jev.native-connector-observer-witness.v1', 'phase': 'dispatching',
         'session_id': row['session_id'], 'actor_unit': row['actor_unit'],
         'checkpoint_sha256': '1' * 64,
         'receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest(),
         'lock_identity': {'device': 1, 'inode': 2},
         'bridge_asset_sha256': connector_observer_bridge_sha256(),
         'command_sha256': hashlib.sha256(connector_snapshot_command(
             row['session_id'], row['actor_unit'], mode=snapshot_mode).encode('utf-8')).hexdigest()},
        {'phase': 'qualified', 'snapshot_tick': snapshot['tick'],
         'snapshot_sha256': connector_snapshot_sha256(snapshot)},
    ]
    path = directory / CONNECTOR_OBSERVER_WITNESS_NAME
    path.write_text(''.join(json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n'
                            for event in rows), encoding='utf-8')
    path.chmod(0o600)
    return receipt, path
