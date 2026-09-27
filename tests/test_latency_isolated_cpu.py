"""Real clock attribution in a fresh interpreter, without unrelated test threads."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_isolated_cpu_work_and_wait_have_distinct_process_accounting():
    source = Path(__file__).resolve().parents[1]/'src'
    program = r'''
import json
import sys
import time
sys.path.insert(0, sys.argv[1])
from jev_factorio.observation import ObservationProfile
waiting = ObservationProfile()
waiting.subcall('other', lambda: time.sleep(0.04))
wait_result = waiting.summary()
working = ObservationProfile()
def busy():
    until = time.process_time_ns() + 20_000_000
    while time.process_time_ns() < until:
        sum(range(50))
working.subcall('other', busy)
print(json.dumps({'wait': wait_result, 'work': working.summary()}))
'''
    # -I ignores site/user PYTHON* configuration. No backend/model is constructed.
    result = subprocess.run([sys.executable, '-I', '-c', program, str(source)],
                            check=True, capture_output=True, text=True, timeout=15)
    data = json.loads(result.stdout)
    wait, work = data['wait'], data['work']
    assert work['process_cpu_ns'] >= 20_000_000
    assert wait['subcalls']['other']['total_ns'] >= 30_000_000
    assert wait['process_cpu_ns'] < work['process_cpu_ns']
    for profile in (wait, work):
        assert profile['subcalls']['other']['count'] == 1
        assert sum(profile['wall_partition_ns'].values()) == profile['total_ns']
        assert sum(profile['process_cpu_partition_ns'].values()) == profile['process_cpu_ns']
