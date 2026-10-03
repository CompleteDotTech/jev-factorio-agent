"""Provider WAL rejects invalid proposals without mutating retained paid history."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from jev_factorio import blocked_persistence as persistence

# Exact published-source082 failure rows; no native/private state required.
ROWS = [{'decision_input_sha256': '831a34d645f1183fbbc778804d98323943b0614d73493009ca5af870081dd22f', 'outcome': 'rejected', 'reason': 'low choice confidence', 'selection_batch': {'frontier_sha256': '498bef35bfeb6697c4b01f7cccdc62babfde9d96774aacc99d1fde882984b341', 'offered': [{'candidate_sha256': 'd03b7ee3c115e70a021222181a41ea154466738f1cfec744d22828b050874742', 'plan_id': 'factory:factory_extract:bootstrap:2545:iron-ore:13'}, {'candidate_sha256': '79d90766e674cc2067d8a66f12c68ca83ac5d462ce051cad8ea3cbeb468e116d', 'plan_id': 'factory:factory_extract:bootstrap:2545:iron-ore:20'}], 'request_sha256': '8f586c374690fb4ef7c0163f3c88c5bedd3b0630aaae003d9505a8b00dffd403', 'schema': 1, 'state_sha256': '14ee28ace7fb9cc88ce6c56c9d371e968e88a9096ea7ae45538b1abd3984fd35'}, 'source_revision': {'commit': '7a0787ed7f6a2e4cb4c0ab23d3af43bf50f50130', 'source_sha256': 'e301619b143bff38274dc1cb46dc08b9afd475c020b4f510ad322c3a63caf878'}, 'tick': 32479477}, {'decision_input_sha256': 'b54aa8a410c602fbe10a8fef49267a9f6d292ab608143f331d5d0f770df079ad', 'outcome': 'pending', 'reason': 'Candidate evidence insufficient', 'selection_batch': {'frontier_sha256': '498bef35bfeb6697c4b01f7cccdc62babfde9d96774aacc99d1fde882984b341', 'offered': [{'candidate_sha256': 'd03b7ee3c115e70a021222181a41ea154466738f1cfec744d22828b050874742', 'plan_id': 'factory:factory_extract:bootstrap:2545:iron-ore:13'}, {'candidate_sha256': '79d90766e674cc2067d8a66f12c68ca83ac5d462ce051cad8ea3cbeb468e116d', 'plan_id': 'factory:factory_extract:bootstrap:2545:iron-ore:20'}], 'request_sha256': 'a86ac7344ee4a29b17a6053d69c8bab5c19a96e4d06153ba825a4f817c4bdbf5', 'schema': 1, 'state_sha256': '14ee28ace7fb9cc88ce6c56c9d371e968e88a9096ea7ae45538b1abd3984fd35'}, 'source_revision': {'commit': '7a0787ed7f6a2e4cb4c0ab23d3af43bf50f50130', 'source_sha256': 'e301619b143bff38274dc1cb46dc08b9afd475c020b4f510ad322c3a63caf878'}, 'tick': 32479902}]

def memory_with_rows(rows):
    source=ROWS[0]['source_revision']
    return SimpleNamespace(session_id='fixture-session',status='blocked',
        reason='low choice confidence',blocked_recovery_archive=None,
        blocked_recovery=dict(schema=1,session_id='fixture-session',
            source_revision=source,attempts=deepcopy(rows),
            last_input_sha256=rows[-1]['decision_input_sha256'] if rows else None,
            wait_level=3))

def test_actual082_duplicate_prepared_batch_rejected_before_wal_mutation():
    memory=memory_with_rows(ROWS[:1]);before=deepcopy(memory.blocked_recovery)
    row=ROWS[1]
    with pytest.raises(ValueError,match='already offered'):
        persistence.record_attempt(memory,row['source_revision'],row['decision_input_sha256'],
            row['reason'],row['tick'],selection_batch=row['selection_batch'])
    assert memory.blocked_recovery==before
    assert before['attempts'][0]['outcome']=='rejected'

def test_prospective_fourth_batch_rejected_without_mutation():
    memory=memory_with_rows([])
    for number in range(persistence.MAX_SELECTION_BATCHES_PER_STATE):
        batch=deepcopy(ROWS[0]['selection_batch'])
        for index,offered in enumerate(batch['offered']):
            offered['candidate_sha256']=format(number*2+index+1,'064x')
        persistence.record_attempt(memory,ROWS[0]['source_revision'],format(number+10,'064x'),
            'low choice confidence',number,selection_batch=batch)
    before=deepcopy(memory.blocked_recovery);batch=deepcopy(ROWS[0]['selection_batch'])
    with pytest.raises(ValueError,match='batch limit'):
        persistence.record_attempt(memory,ROWS[0]['source_revision'],'f'*64,
            'low choice confidence',10,selection_batch=batch)
    assert memory.blocked_recovery==before

def test_distinct_candidate_batch_preserves_both_pending_and_paid_rejection():
    memory=memory_with_rows(ROWS[:1]);batch=deepcopy(ROWS[1]['selection_batch'])
    for index,offered in enumerate(batch['offered']):offered['candidate_sha256']=format(index+1,'064x')
    persistence.record_attempt(memory,ROWS[1]['source_revision'],ROWS[1]['decision_input_sha256'],
        ROWS[1]['reason'],ROWS[1]['tick'],selection_batch=batch)
    assert memory.blocked_recovery['attempts'][0]==ROWS[0]
    assert memory.blocked_recovery['attempts'][1]['outcome']=='pending'
    assert len(memory.blocked_recovery['attempts'])==2
