"""Read-only compatibility audit for the FLE release used by native timing."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from importlib import metadata


DISTRIBUTION = 'factorio-learning-environment'
RELEASE_VERSION = '0.4.3'
RELEASE_COMMIT = '6439e18b7870770454cf91eb36b3d1e6412724f4'
SOURCE_SHA256 = {
    'fle/env/tools/controller.py': 'c932d2e3c57c4796fb18462a927c87e8dbee1980af49161b3a66b9c659db3c3b',
    'fle/env/tools/agent/get_entity/client.py': '7887f71e559b7d63dd923e8cde0e1167ec31bbfbb410f38be9d84d7fb450c810',
    'fle/env/tools/agent/get_entities/client.py': 'bf789e2a7f679c090fb5d354784a8c51b091f18942574e5f169a5238e512971c',
    'fle/env/tools/admin/get_path/client.py': 'af9e7bac3b3c0ef51a0045a029c71d0813624d380b8ea0e142b9dcf7053e2ba8',
    'fle/env/tools/agent/connect_entities/client.py': 'b09f37536828e1c648b757c34b6d16b0ccf933d8f23831dd79bb49f8df51580f',
}


def audit_distribution(distribution) -> dict:
    """Compare installed, relevant FLE source bytes with the pinned release."""
    installed = {
        str(path).replace('\\', '/'): path
        for path in (distribution.files or ())
    }
    source_results = {}
    for relative, expected_hash in SOURCE_SHA256.items():
        entry = installed.get(relative)
        observed_hash = None
        if entry is not None:
            try:
                observed_hash = hashlib.sha256(
                    distribution.locate_file(entry).read_bytes()
                ).hexdigest()
            except OSError:
                pass
        source_results[relative] = {
            'expected_sha256': expected_hash,
            'observed_sha256': observed_hash,
            'matches_release': observed_hash == expected_hash,
        }

    version = distribution.version
    compatible = (version == RELEASE_VERSION
                  and all(row['matches_release'] for row in source_results.values()))
    return {
        'schema': 1,
        'distribution': DISTRIBUTION,
        'expected_version': RELEASE_VERSION,
        'observed_version': version,
        'release_commit': RELEASE_COMMIT,
        'source_files': source_results,
        'compatible': compatible,
        'scope': 'installed_source_identity_only_not_native_performance',
        'private_metadata_emitted': False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        distribution = metadata.distribution(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        print(json.dumps({
            'schema': 1,
            'distribution': DISTRIBUTION,
            'expected_version': RELEASE_VERSION,
            'compatible': False,
            'reason': 'distribution_not_installed',
            'scope': 'installed_source_identity_only_not_native_performance',
            'private_metadata_emitted': False,
        }, sort_keys=True))
        return 2

    result = audit_distribution(distribution)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['compatible'] else 1


if __name__ == '__main__':
    sys.exit(main())
