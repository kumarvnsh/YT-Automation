import json

import pytest

from src import pipeline, research
from src.config import Config


def story(weak_count=1, good_count=4, weak_status='supported'):
    sources, claims, cites = [], [], []
    for i in range(good_count):
        url = f'https://www.loc.gov/item/{i}'
        sources.append({'id': f'S{i}', 'url': url, 'title': f'Record {i}'})
        claims.append({'id': f'C{i}', 'text': f'Documented fact {i}.', 'status': 'supported',
                       'source_ids': [f'S{i}'], 'material_to_core_story': True})
        cites.append({'url': url, 'cited_text': f'Documented fact {i}.'})
    for j in range(weak_count):
        url = f'https://unapproved.example.com/sample{j}.pdf'
        sources.append({'id': f'W{j}', 'url': url, 'title': f'Sample {j}'})
        claims.append({'id': f'X{j}', 'text': f'Detail from a weak source {j}.', 'status': weak_status,
                       'source_ids': [f'W{j}'], 'material_to_core_story': True})
    return {'sources': sources, 'claims': claims, 'locations': [], 'dates': []}, cites


def run(raw, cites, monkeypatch):
    monkeypatch.setattr(research, '_retrieve', lambda *a, **k: (raw, cites))
    return research.research_topic(Config({}), 'canned food')


def test_one_weak_source_claim_is_pruned_and_story_proceeds(monkeypatch):
    result = run(*story(), monkeypatch)
    assert result['status'] == 'supported', result.get('errors')
    assert [c['id'] for c in result['claims']] == ['C0', 'C1', 'C2', 'C3']
    assert result['pruned_claims']['claim_ids'] == ['X0']
    assert result['excluded_claims'][-1]['excluded_reason'] == 'weak_source_pruned'
    # Dropped claims never reach the script writer.
    assert 'weak source' not in json.dumps(research.evidence_for_model(result))
    assert research.validate_research(Config({}), result) == []


def test_disputed_claim_is_never_pruned(monkeypatch):
    result = run(*story(weak_status='disputed'), monkeypatch)
    assert result['status'] == 'unsupported'
    assert 'pruned_claims' not in result


def test_pruning_never_leaves_a_thin_story(monkeypatch):
    result = run(*story(weak_count=1, good_count=2), monkeypatch)
    assert result['status'] == 'unsupported'


def test_pruning_never_drops_more_than_a_third(monkeypatch):
    result = run(*story(weak_count=3, good_count=4), monkeypatch)
    assert result['status'] == 'unsupported'


def test_other_policy_errors_are_not_relaxed():
    packet = {'status': 'unsupported', 'claims': [], 'errors': ['Required geography has no vetted map asset']}
    assert research.prune_weak_claims(Config({}), packet) is packet


SELECTION = {'candidates': [
    {'premise': 'Canning began as a provisioning prize', 'opening_visual': 'jar', 'payoff': 'p', 'viewer_reason': 'v'},
    {'premise': 'Second distinct premise', 'rejection_reason': 'weaker'},
    {'premise': 'Third distinct premise', 'rejection_reason': 'weaker'}],
    'selected_index': 0, 'selection_reason': 'clearest'}


def growth_state():
    return {'growth_trial': {'id': 'trial'}, 'overrides': {'topic': 'Everyday origins'}}


def test_growth_trial_retries_once_with_feedback(tmp_path, monkeypatch):
    calls = []
    bad = {'status': 'unsupported', 'errors': ['Claim c3: lacks authoritative directly cited support'],
           'claims': [], 'editorial_selection': SELECTION}
    good = {'status': 'supported', 'claims': [], 'sources': []}  # selection omitted on purpose

    def fake(cfg, topic, recovery_request=None):
        calls.append(recovery_request)
        return bad if len(calls) == 1 else good
    monkeypatch.setattr(research, 'begin_step', lambda *a: [])
    monkeypatch.setattr(research, 'research_topic', fake)
    monkeypatch.setattr('src.map_assets.required_maps', lambda r: [])
    state = growth_state()
    pipeline._step_research(Config({}), tmp_path, state)
    assert calls[0] is None and calls[1]['errors'] == bad['errors']
    assert state['research']['status'] == 'supported'
    assert state['editorial_selection']['selected_index'] == 0
    assert (tmp_path / 'research_attempt_2.json').exists()


def test_growth_trial_still_stops_when_retry_fails(tmp_path, monkeypatch):
    bad = {'status': 'unsupported', 'errors': ['Claim c3: lacks authoritative directly cited support'],
           'claims': [], 'editorial_selection': SELECTION}
    calls = []
    monkeypatch.setattr(research, 'begin_step', lambda *a: [])
    monkeypatch.setattr(research, 'research_topic', lambda cfg, topic, recovery_request=None: calls.append(1) or bad)
    with pytest.raises(ValueError, match='needs supported research'):
        pipeline._step_research(Config({}), tmp_path, growth_state())
    assert len(calls) == 2


def test_retry_can_be_disabled(tmp_path, monkeypatch):
    bad = {'status': 'unsupported', 'errors': ['x'], 'claims': [], 'editorial_selection': SELECTION}
    calls = []
    monkeypatch.setattr(research, 'begin_step', lambda *a: [])
    monkeypatch.setattr(research, 'research_topic', lambda cfg, topic, recovery_request=None: calls.append(1) or bad)
    with pytest.raises(ValueError):
        pipeline._step_research(Config({'research': {'growth_retry_attempts': 0}}), tmp_path, growth_state())
    assert len(calls) == 1

def test_material_failure_reselects_independent_story_without_carryover(tmp_path, monkeypatch):
    bad = {'status': 'unsupported', 'findings': [{'text': 'Old story contradiction',
           'status': 'unsupported', 'material_to_core_story': True}], 'editorial_selection': SELECTION}
    good = {'status': 'supported', 'claims': [], 'sources': [], 'editorial_selection': SELECTION}
    calls = []
    def fake(cfg, topic, recovery_request=None):
        calls.append((topic, recovery_request))
        return bad if len(calls) == 1 else good
    monkeypatch.setattr(research, 'begin_step', lambda *a: [])
    monkeypatch.setattr(research, 'research_topic', fake)
    monkeypatch.setattr('src.map_assets.required_maps', lambda r: [])
    state = {'growth_trial': {'id': 'trial'}, 'category': 'history', 'overrides': {}}
    pipeline._step_research(Config({}), tmp_path, state)
    assert len(calls) == 2 and calls[1][1] is None
    assert 'DISTINCT' in calls[1][0]
    assert state['research']['status'] == 'supported'
    assert json.loads((tmp_path / 'research_attempt_1.json').read_text())['findings'] == bad['findings']
