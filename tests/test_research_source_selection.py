import unittest
from types import SimpleNamespace
from unittest.mock import patch
from src import research
from src.config import Config

class ResearchSourceSelectionTests(unittest.TestCase):
    def test_approved_domains_are_explicit_in_initial_and_repair_prompts(self):
        cfg=Config({'research':{'authoritative_domains':['vam.ac.uk','loc.gov']}})
        for kwargs in ({},{'evidence_request':{'claims':[],'sources':[]}}, {'recovery_request':{'errors':['unapproved arxiv.org']}}):
            client=SimpleNamespace(messages=SimpleNamespace(create=None))
            response=SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(type='text',text='{"sources":[],"claims":[]}',citations=[])])
            with patch.object(research,'_client',return_value=client), patch.object(client.messages,'create',return_value=response) as create:
                research._retrieve(cfg,'a textile history',**kwargs)
            prompt=create.call_args.kwargs['messages'][0]['content']
            self.assertIn('vam.ac.uk',prompt)
            self.assertIn('loc.gov',prompt)
            self.assertIn('site:',prompt)
            self.assertIn('exactly one JSON',prompt)
