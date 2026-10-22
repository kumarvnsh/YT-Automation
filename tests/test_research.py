import unittest
from unittest.mock import patch
from src.config import Config
from src import research

FETCH_PAGE = research._fetch_page

class ResearchTests(unittest.TestCase):
    def setUp(self):
        offline = patch.object(research, '_fetch_page', side_effect=RuntimeError('offline test boundary'))
        offline.start()
        self.addCleanup(offline.stop)

    def test_direct_page_text_repairs_missing_citation_without_model_claim_changes(self):
        packet = self.packet()
        citation = {'url': packet['sources'][0]['url'], 'cited_text': 'The border was agreed in 1818.', 'type': 'retrieved_page'}
        with patch.object(research, '_retrieve', return_value=(packet, [])) as retrieve, \
                patch.object(research, '_fetch_page', return_value=citation):
            result = research.research_topic(Config({}), 'border')
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(result['claims'], packet['claims'])
        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(result['retrieval_citations'][0]['type'], 'retrieved_page')

    def test_unapproved_page_is_never_fetched(self):
        with patch('requests.get') as request:
            with self.assertRaises(ValueError):
                FETCH_PAGE(Config({}), 'https://untrusted.example/fact')
        request.assert_not_called()

    def test_page_parser_ignores_scripts_and_navigation(self):
        parser = research._ArticleText()
        parser.feed('<html><nav>Menu</nav><script>not evidence</script><main><h1>History</h1><p>Border in 1818.</p></main></html>')
        self.assertEqual(' '.join(parser.parts), 'History Border in 1818.')

    def test_model_context_keeps_evidence_without_duplicate_fetch_logs(self):
        packet = {**self.packet(), 'retrieval_citations': [{'cited_text': 'duplicate'}], 'evidence_attempts': ['old evidence']}
        context = research.evidence_for_model(packet)
        self.assertEqual(context['claims'], packet['claims'])
        self.assertEqual(context['sources'], packet['sources'])
        self.assertNotIn('retrieval_citations', context)
        self.assertNotIn('evidence_attempts', context)

    def test_research_missing_citation_makes_no_paid_repair_call(self):
        packet = self.packet()
        with patch.object(research, '_retrieve', return_value=(packet, [])) as retrieve, \
                patch.object(research, '_fetch_page', side_effect=ValueError('offline')):
            result = research.research_topic(Config({}), 'border')
        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(result['claims'], packet['claims'])

    def test_failed_repair_retains_original_evidence_and_actionable_claim_error(self):
        packet = self.packet()
        with patch.object(research, '_retrieve', return_value=(packet, [])) as retrieve:
            result = research.research_topic(Config({}), 'border')
        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['claims'], packet['claims'])
        self.assertTrue(any('c1' in error for error in result['errors']))

    def test_fragment_link_matches_actual_page_citation_without_network(self):
        packet = self.packet()
        packet['sources'][0]['url'] += '#history'
        citation = {'url': 'https://www.loc.gov/item/1', 'cited_text': 'The border was agreed in 1818.'}
        with patch.object(research, '_retrieve', return_value=(packet, [citation])) as retrieve:
            result = research.research_topic(Config({}), 'border')
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(retrieve.call_count, 1)

    def test_multiple_sections_of_same_page_validate_consistently(self):
        packet = self.packet()
        citations = [{'url': packet['sources'][0]['url'] + fragment, 'cited_text': text}
                     for fragment, text in [('#first', 'The border was agreed'), ('#second', 'in 1818.')]]
        with patch.object(research, '_retrieve', return_value=(packet, citations)):
            result = research.research_topic(Config({}), 'border')
        self.assertEqual(result['status'], 'supported')

    def packet(self):
        return {'sources':[{'id':'s1','url':'https://www.loc.gov/item/1','title':'Record','publisher':'Library of Congress','excerpt':'The border was agreed in 1818.'}], 'claims':[{'id':'c1','text':'The border was agreed in 1818.','source_ids':['s1'],'status':'supported'}], 'locations':[], 'dates':['1818']}
    def test_hash_changes_when_narration_changes(self):
        self.assertNotEqual(research.script_hash({'segments':[{'narration':'1818'}]}), research.script_hash({'segments':[{'narration':'1819'}]}))
    def test_retrieval_never_trusts_invented_citations(self):
        with patch.object(research,'_retrieve',return_value=(self.packet(), [])):
            result=research.research_topic(Config({}), 'border')
        self.assertEqual(result['status'],'unsupported')
    def test_supported_retrieved_claims_pass(self):
        packet=self.packet()
        citations=[{'url':packet['sources'][0]['url'],'cited_text':packet['sources'][0]['excerpt']}]
        with patch.object(research,'_retrieve',return_value=(packet,citations)):
            result=research.research_topic(Config({}), 'border')
        self.assertEqual(result['status'],'supported')
        script={'segments':[{'narration':'The border was agreed in 1818.','claim_ids':['c1']}]}
        assessment={'segments':[{'segment_id':0,'status':'supported','claim_ids':['c1'],'reason':'Exact supporting excerpt.'}]}
        with patch.object(research,'_assess',return_value=assessment):
            report=research.verify_script(Config({}),script,result)
        self.assertEqual(report['status'],'supported')
        self.assertEqual(report['script_hash'],research.script_hash(script))
    def test_unsupported_segment_blocks_verification(self):
        packet=self.packet();packet['status']='supported'
        with patch.object(research,'_assess',return_value={'segments':[]}):
            report=research.verify_script(Config({}),{'segments':[{'narration':'Invented.'}]},packet)
        self.assertEqual(report['status'],'unsupported')
    def test_failure_is_recorded(self):
        with patch.object(research,'_retrieve',side_effect=RuntimeError('offline')):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'retrieval_failed')
    def test_disputed_date_cannot_pass(self):
        packet=self.packet();packet['claims'][0]['status']='disputed'
        citation={'url':packet['sources'][0]['url'],'cited_text':'The date is disputed.'}
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'unsupported')
    def test_extraordinary_claim_requires_independent_authorities(self):
        packet=self.packet();packet['claims'][0]['extraordinary']=True
        citation={'url':packet['sources'][0]['url'],'cited_text':packet['sources'][0]['excerpt']}
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'unsupported')
    def test_unknown_authority_cannot_pass(self):
        packet=self.packet();packet['sources'][0]['url']='https://untrusted.example/fact'
        citation={'url':packet['sources'][0]['url'],'cited_text':packet['sources'][0]['excerpt']}
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'unsupported')
    def test_same_authority_subdomains_are_not_independent(self):
        packet=self.packet();packet['claims'][0].update(extraordinary=True,source_ids=['s1','s2'])
        packet['sources'].append(dict(packet['sources'][0],id='s2',url='https://catalog.loc.gov/item/2'))
        citations=[{'url':s['url'],'cited_text':s['excerpt']} for s in packet['sources']]
        with patch.object(research,'_retrieve',return_value=(packet,citations)):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'unsupported')
    def test_related_institution_domains_are_not_independent(self):
        cfg = Config({'research': {'authoritative_domains': ['qdl.qa', 'qnl.qa'],
                                  'publisher_aliases': {'qdl.qa': 'qnl.qa'}}})
        claim = {'id': 'C1', 'status': 'supported', 'extraordinary': True, 'source_ids': ['S1', 'S2']}
        sources = {'S1': {'url': 'https://qdl.qa/item/1', 'citation_verified': True},
                   'S2': {'url': 'https://qnl.qa/item/2', 'citation_verified': True}}
        self.assertFalse(research._claim_ok(cfg, claim, sources))
    def test_legacy_location_strings_cannot_silently_skip_maps(self):
        packet=self.packet();packet['locations']=['canada']
        citation={'url':packet['sources'][0]['url'],'cited_text':packet['sources'][0]['excerpt']}
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'unsupported')
    def test_required_location_cannot_have_missing_map(self):
        packet=self.packet();packet['locations']=[{'name':'Canada','map_id':None,'requires_map':True}]
        citation={'url':packet['sources'][0]['url'],'cited_text':packet['sources'][0]['excerpt']}
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'unsupported')
    def test_unvetted_historical_overlay_is_rejected(self):
        packet=self.packet();packet['historical_overlays']=[{'date':'1818','claim_ids':['c1']}]
        citation={'url':packet['sources'][0]['url'],'cited_text':packet['sources'][0]['excerpt']}
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'unsupported')
    def test_json_extraction_handles_prose_braces(self):
        self.assertEqual(research._json('A claim {not JSON}. Final packet:\n{"claims":[]}\nSources {citations}'),{'claims':[]})
    def test_provider_web_search_error_is_actionable(self):
        from types import SimpleNamespace
        response=SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(type='web_search_tool_result',content={'type':'web_search_tool_result_error','error_code':'unavailable'})])
        client=SimpleNamespace(messages=SimpleNamespace(create=lambda **_:response))
        with patch.object(research,'_client',return_value=client):
            result=research.research_topic(Config({}),'border')
        self.assertEqual(result['status'],'retrieval_failed')
        self.assertIn('unavailable',result['error'])
    def test_paused_web_search_is_not_a_completed_research_packet(self):
        from types import SimpleNamespace
        response=SimpleNamespace(stop_reason='pause_turn',content=[])
        client=SimpleNamespace(messages=SimpleNamespace(create=lambda **_:response))
        with patch.object(research,'_client',return_value=client):
            result=research.research_topic(Config({}),'border')
        self.assertIn('pause_turn',result['error'])
    def test_default_search_is_restricted_to_vetted_authorities(self):
        from types import SimpleNamespace
        calls=[]
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(stop_reason='end_turn',content=[])
        with patch.object(research,'_client',return_value=SimpleNamespace(messages=SimpleNamespace(create=create))):
            research.research_topic(Config({}),'border')
        self.assertIn('loc.gov',calls[0]['tools'][0]['allowed_domains'])
    def test_nonmaterial_dispute_is_retained_but_material_contradiction_blocks(self):
        packet=self.packet();packet['findings']=[{'text':'Invented extra naming story','status':'disputed','material_to_core_story':False}]
        citation={'url':packet['sources'][0]['url'],'cited_text':packet['sources'][0]['excerpt']}
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            result=research.research_topic(Config({}),'border')
        self.assertEqual(result['status'],'supported')
        self.assertEqual(result['findings'],packet['findings'])
        packet['findings'][0]['material_to_core_story']=True
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            self.assertEqual(research.research_topic(Config({}),'border')['status'],'unsupported')
    def test_uncited_auxiliary_source_cannot_block_cited_core_evidence(self):
        packet=self.packet();packet['sources'].append({'id':'unused','url':'https://loc.gov/unused','excerpt':'Unretrieved','title':'Auxiliary','publisher':'LoC'})
        citation={'url':packet['sources'][0]['url'],'cited_text':packet['sources'][0]['excerpt']}
        with patch.object(research,'_retrieve',return_value=(packet,[citation])):
            result=research.research_topic(Config({}),'border')
        self.assertEqual(result['status'],'supported')
        self.assertFalse(result['sources'][1]['citation_verified'])
