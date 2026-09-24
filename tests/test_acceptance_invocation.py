"""Synthetic invocation-boundary regressions, not native VM runs."""
import pytest

from jev_factorio.acceptance_capture import capture
from jev_factorio.native_acceptance import analyze
from test_native_acceptance import source_files, rewrite


@pytest.mark.parametrize('key',['process_id','execution_id'])
def test_restarted_capture_is_ineligible_even_when_counters_remain_monotonic(tmp_path,key):
    args,rows=source_files(tmp_path/'inputs')
    rows[-1][key]='new-invocation'
    rewrite(args,rows);capture(**args,environ={})
    report=analyze(args['output'])
    assert 'interrupted_or_mixed_invocation' in report['issues']
    assert not report['measurement_checks_passed']


@pytest.mark.parametrize('key',['process_id','execution_id'])
def test_missing_invocation_identity_is_unknown_not_a_continuous_run(tmp_path,key):
    args,rows=source_files(tmp_path/'inputs')
    for row in rows:row.pop(key)
    rewrite(args,rows);capture(**args,environ={})
    report=analyze(args['output'])
    assert 'invocation_identity_missing' in report['issues']
    assert not report['measurement_checks_passed']


def test_same_commit_with_other_source_bytes_does_not_match_trial(tmp_path):
    args,rows=source_files(tmp_path/'inputs')
    for row in rows:row['code_revision']['source_sha256']='c'*64
    rewrite(args,rows);capture(**args,environ={})
    report=analyze(args['output'])
    assert 'source_revision_mismatch' in report['issues']
    assert not report['measurement_checks_passed']
