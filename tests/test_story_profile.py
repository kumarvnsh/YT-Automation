import unittest
from unittest.mock import patch
from src.config import Config
from src.script_generator import validate_structure, _build_prompt, _parse_script_response
import json

class StoryProfileTests(unittest.TestCase):
    def test_payoff_need_not_end_in_question(self):
        beats=['hook','setup','pivot','fact','fact','fact','callback']
        narration=['Americans cross Canada to reach home.','The border created the problem.','A treaty fixed the line.','The map was incomplete.','Roads followed later.','The route crosses two borders.','That border explains why Americans drive through Canada to reach home.']
        self.assertIsNone(validate_structure(beats,narration,'consequence_first'))
        narration[0]='In 1818, Americans agreed a border.'
        self.assertIn('date',validate_structure(beats,narration,'consequence_first'))
    @patch('src.script_generator.performance_examples',return_value=([],[]))
    @patch('src.script_generator.recent_titles',return_value=[])
    def test_prompt_supplies_evidence_and_consequence_budget(self,*_):
        cfg=Config({'script':{'story_profile':'consequence_first'},'production':{'research_context':{'claims':[{'id':'c1'}]}}})
        prompt=_build_prompt(cfg,'short','border')
        self.assertIn('first 2–3 spoken seconds',prompt)
        self.assertIn('claim_ids',prompt)
        self.assertNotIn('MUST be a real opinion/verdict question',prompt)
    def test_claim_references_survive_parse(self):
        _,segments=_parse_script_response(json.dumps({'title':'x','description':'y','segments':[{'narration':'Text','claim_ids':['c1']}]}))
        self.assertEqual(segments[0].claim_ids,['c1'])
