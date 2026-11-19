import json
from datetime import datetime, timezone

from src import creative

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
INSTRUCTION = 'Open with the single most surprising number in the first sentence.'


def started(start_slot=200):
    return creative.start_test(creative.empty(), INSTRUCTION, 'numbers stop the scroll', start_slot, NOW)


def test_alternate_slots_split_into_variant_and_control():
    g = started()
    arms = [creative.arm(g, s) for s in range(200, 200 + creative.POSTS)]
    assert [a['arm'] for a in arms[:4]] == ['variant', 'control', 'variant', 'control']
    assert sum(a['arm'] == 'variant' for a in arms) == creative.POSTS // 2
    assert arms[0]['instruction'] == INSTRUCTION and arms[1]['instruction'] is None
    assert creative.arm(g, 199) is None and creative.arm(g, 200 + creative.POSTS) is None


def test_measuring_tests_still_assign_in_flight_slots():
    g = started()
    g['measuring'].append(g['active'])
    g['active'] = None
    assert creative.arm(g, 201)['arm'] == 'control'


def test_learned_rules_reach_every_prompt_and_variant_adds_its_instruction():
    g = started()
    g['learned'] = [{'id': 'old', 'instruction': 'Keep sentences under 15 words.'}]
    assert creative.prompt_instructions(g, creative.arm(g, 201)) == ['Keep sentences under 15 words.']
    assert creative.prompt_instructions(g, creative.arm(g, 200))[-1] == INSTRUCTION
    block = creative.prompt_block(creative.prompt_instructions(g, None))
    assert 'never overrides accuracy' in block and 'Keep sentences under 15 words.' in block
    assert creative.prompt_block([]) == ''


def test_instruction_validation():
    g = started()
    assert creative.validate_instruction('Lead with the reveal, then explain how it happened.', g) == []
    assert creative.validate_instruction(INSTRUCTION, g)  # already being tested
    assert creative.validate_instruction('short', g)
    assert creative.validate_instruction('Exaggerate the stakes so the hook feels bigger than it is.', g)
    assert creative.validate_instruction('Skip the research claims when they slow the story down.', g)
    assert creative.validate_instruction('Tell viewers to visit https://example.com for the full story.', g)


def test_window_and_new_test_moves_previous_to_measuring():
    g = started(200)
    assert not creative.window_full(g, 210)
    assert creative.window_full(g, 200 + creative.POSTS - 1)
    nxt = creative.start_test(g, 'Name the place and year in the first five words.', 'h', 225, NOW)
    assert nxt['active']['start_slot'] == 225 and nxt['measuring'][0]['id'] == g['active']['id']


def test_judge_and_conclude():
    assert creative.judge([0.6] * 5, [0.5] * 9) == ('wait', None)
    assert creative.judge([0.6] * 7, [0.5] * 7) == ('promote', 0.1)
    assert creative.judge([0.022] * 7, [0.02] * 7)[0] == 'drop'
    g = started()
    g['measuring'].append(g['active'])
    g['active'] = None
    done = creative.conclude(g, g['measuring'][0]['id'], 'promote', {'lift': 0.1}, NOW)
    assert done['measuring'] == [] and done['learned'][0]['instruction'] == INSTRUCTION
    assert done['history'][0]['decision'] == 'promote'
    dropped = creative.conclude(g, g['measuring'][0]['id'], 'drop', {'lift': -0.02}, NOW)
    assert dropped['learned'] == []


def test_load_tolerates_missing_or_partial_file(tmp_path):
    assert creative.load(tmp_path) == creative.empty()
    (tmp_path / 'data').mkdir()
    (tmp_path / creative.PATH).write_text(json.dumps({'learned': [{'id': 'a', 'instruction': 'x' * 30}]}))
    loaded = creative.load(tmp_path)
    assert loaded['active'] is None and len(loaded['learned']) == 1
