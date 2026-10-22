import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import pipeline, research
from src.config import Config


def packet():
    return {
        'sources': [{'id': 'S1', 'url': 'https://loc.gov/item/1', 'title': 'Record'}],
        'claims': [{'id': 'C1', 'text': 'A documented event.', 'status': 'supported', 'source_ids': ['S1']}],
        'locations': [], 'dates': [],
    }


def citations():
    return [{'url': 'https://loc.gov/item/1', 'cited_text': 'A documented event.'}]


class ResearchRecoveryTests(unittest.TestCase):
    def test_explicitly_optional_dispute_moves_to_findings_and_is_audited(self):
        raw = packet()
        optional = {'id': 'C7', 'text': 'Disputed background.', 'status': 'disputed',
                    'source_ids': ['S6'], 'material_to_core_story': False}
        raw['claims'].append(optional)
        with patch.object(research, '_retrieve', return_value=(raw, citations())):
            result = research.research_topic(Config({}), 'bridge')
        self.assertEqual(result['status'], 'supported')
        self.assertEqual([c['id'] for c in result['claims']], ['C1'])
        self.assertEqual(result['findings'], [optional])
        self.assertEqual(result['excluded_claims'], [optional])
        self.assertEqual(raw['claims'][-1], optional)

    def test_unclassified_or_material_dispute_still_blocks(self):
        for relevance in (None, True):
            raw = packet()
            disputed = {'id': 'C7', 'text': 'Disputed cause.', 'status': 'disputed', 'source_ids': ['S1']}
            if relevance is not None:
                disputed['material_to_core_story'] = relevance
            raw['claims'].append(disputed)
            with patch.object(research, '_retrieve', return_value=(raw, citations())):
                result = research.research_topic(Config({}), 'bridge')
            self.assertEqual(result['status'], 'unsupported')
            self.assertEqual(len(result['claims']), 2)

    def test_removing_optional_claims_cannot_leave_empty_production(self):
        raw = packet()
        raw['claims'][0].update(status='disputed', material_to_core_story=False)
        with patch.object(research, '_retrieve', return_value=(raw, citations())):
            self.assertEqual(research.research_topic(Config({}), 'bridge')['status'], 'unsupported')

    def run_stage(self, responses, overrides=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        stage = Path(temporary.name)
        state = {'overrides': overrides or {}, 'category': 'historical_disasters', 'experiment_hook': 'Unexpected failure'}
        with patch.object(research, 'research_topic', side_effect=responses) as retrieve:
            pipeline._step_research(Config({}), stage, state)
        return state, stage, retrieve

    def good(self):
        with patch.object(research, '_retrieve', return_value=(packet(), citations())):
            return research.research_topic(Config({}), 'bridge')

    def bad(self):
        return {'topic': 'bridge', 'status': 'unsupported', 'errors': ['Claim C7 disputed'],
                'claims': [{'id': 'C7', 'text': 'Tacoma flutter is disputed'}]}

    def test_recovery_prompt_contains_errors_without_duplicate_audit_bodies(self):
        from types import SimpleNamespace
        prior = self.bad()
        prior['evidence_attempts'] = [{'large_audit': 'must not enter prompt'}]
        response = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(
            type='text', text=json.dumps(packet()), citations=[])])
        with patch.object(research, '_client') as client:
            client.return_value.messages.create.return_value = response
            research._retrieve(Config({}), 'bridge', recovery_request=prior)
        prompt = client.return_value.messages.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('Claim C7 disputed', prompt)
        self.assertIn('Tacoma flutter', prompt)
        self.assertNotIn('must not enter prompt', prompt)

    def test_research_unsupported_report_continues_after_one_call(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(research, 'research_topic', return_value=self.bad()) as retrieve:
                state = {'category': 'historical_disasters'}
                pipeline._step_research(Config({}), Path(folder), state)
            self.assertEqual(retrieve.call_count, 1)
            self.assertEqual(state['research']['status'], 'unsupported')
            self.assertEqual(json.loads((Path(folder) / 'research.json').read_text())['status'], 'unsupported')

    def test_first_success_has_no_extra_research_calls(self):
        _, _, retrieve = self.run_stage([self.good()])
        self.assertEqual(retrieve.call_count, 1)

    def test_material_findings_survive_same_topic_retry(self):
        bad = self.bad()
        bad['findings'] = [{'text': 'Core cause contradicted.', 'status': 'disputed', 'material_to_core_story': True}]
        with patch.object(research, '_retrieve', return_value=(packet(), citations())):
            result = research.research_topic(Config({}), 'bridge', recovery_request=bad)
        self.assertEqual(result['status'], 'unsupported')
        self.assertIn(bad['findings'][0], result['findings'])

    def test_retry_cannot_erase_material_or_unclassified_disputed_claim(self):
        for relevance in (None, True):
            bad = self.bad()
            bad['claims'][0]['status'] = 'disputed'
            if relevance is not None:
                bad['claims'][0]['material_to_core_story'] = relevance
            with patch.object(research, '_retrieve', return_value=(packet(), citations())):
                result = research.research_topic(Config({}), 'bridge', recovery_request=bad)
            self.assertEqual(result['status'], 'unsupported')
            self.assertTrue(result['findings'][0]['material_to_core_story'])

    def test_retry_can_explicitly_classify_unclassified_dispute_as_background(self):
        bad = self.bad()
        bad['claims'][0]['status'] = 'disputed'
        raw = packet()
        raw['findings'] = [dict(bad['claims'][0], material_to_core_story=False)]
        with patch.object(research, '_retrieve', return_value=(raw, citations())):
            result = research.research_topic(Config({}), 'bridge', recovery_request=bad)
        self.assertEqual(result['status'], 'supported')

    def test_retry_cannot_reclassify_explicitly_material_dispute(self):
        bad = self.bad()
        bad['claims'][0].update(status='disputed', material_to_core_story=True)
        raw = packet()
        raw['findings'] = [dict(bad['claims'][0], material_to_core_story=False)]
        with patch.object(research, '_retrieve', return_value=(raw, citations())):
            result = research.research_topic(Config({}), 'bridge', recovery_request=bad)
        self.assertEqual(result['status'], 'unsupported')
