import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src import pipeline
from src.script_generator import _build_prompt, validate_structure
from src.editorial import assignment, brief


class EditorialTests(unittest.TestCase):
    def config(self):
        return Config({'production': {'enabled': True}, 'editorial': {'enabled': True,
            'regions': [{'id': 'india', 'label': 'India'}, {'id': 'africa', 'label': 'African countries'}],
            'series': [{'id': 'origins', 'name': 'You Use It Every Day', 'direction': 'Everyday origins'},
                       {'id': 'myths', 'name': 'History Got This Wrong', 'direction': 'Misconceptions'},
                       {'id': 'decisions', 'name': 'One Decision Changed Everything', 'direction': 'Choices'}],
            'duration_variants': [{'id': 'concise', 'seconds': 30}, {'id': 'story', 'seconds': 50}],
            'experiment_id': 'facebook-world-history-4w', 'experiment_posts': 56}})

    def test_rotation_covers_each_region_series_duration_combination(self):
        rows = [assignment(self.config(), i) for i in range(12)]
        self.assertEqual(len({(r['region']['id'], r['series']['id'], r['duration_variant']) for r in rows}), 12)
        self.assertEqual([r['region']['id'] for r in rows[:4]], ['india', 'africa', 'india', 'africa'])
        self.assertEqual(sum(r['duration_variant'] == 'concise' for r in rows), 6)

    def test_brief_uses_evergreen_region_and_recent_topic_exclusions(self):
        text = brief(assignment(self.config(), 1), ['Old border story'])
        self.assertIn('African countries', text)
        self.assertIn('specific country', text)
        self.assertIn('Old border story', text)
        self.assertIn('anniversary', text)
        self.assertIn('History Got This Wrong', text)

    def test_inflight_stages_reserve_distinct_assignments_and_snapshot_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(pipeline, 'base_dir', return_value=root):
                a = pipeline.new_stage(self.config(), 'short', no_upload=True, dry_run=False)
                b = pipeline.new_stage(self.config(), 'short', no_upload=True, dry_run=False)
            first, second = pipeline.load_state(a), pipeline.load_state(b)
            self.assertEqual(first['editorial_slot'], 0)
            self.assertEqual(second['editorial_slot'], 1)
            self.assertEqual(first['production_config']['script']['shorts_target_seconds'], 30)
            self.assertEqual(second['production_config']['script']['shorts_target_seconds'], 50)
            self.assertEqual(first['editorial_region'], 'india')
            self.assertEqual(first['experiment'], 'facebook-world-history-4w')

    def test_manual_topic_and_other_channels_do_not_get_experiment_assignments(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, 'base_dir', return_value=Path(tmp)):
            a = pipeline.new_stage(self.config(), 'short', topic='My chosen topic', no_upload=True, dry_run=False)
            b = pipeline.new_stage(Config({}), 'short', no_upload=True, dry_run=False)
            self.assertNotIn('editorial', pipeline.load_state(a))
            self.assertNotIn('editorial', pipeline.load_state(b))

    def test_dry_run_does_not_consume_a_publishing_assignment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, 'base_dir', return_value=Path(tmp)):
            preview = pipeline.new_stage(self.config(), 'short', no_upload=True, dry_run=True)
            actual = pipeline.new_stage(self.config(), 'short', no_upload=True, dry_run=False)
            self.assertTrue(pipeline.load_state(preview)['editorial_preview'])
            self.assertEqual(pipeline.load_state(actual)['editorial_slot'], 0)

    @patch('src.script_generator.performance_examples', return_value=([], []))
    @patch('src.script_generator.recent_titles', return_value=[])
    def test_concise_prompt_delivers_answer_and_fb_follow_without_forced_comment_bait(self, *_):
        cfg = Config({'script': {'story_profile': 'evergreen_concise', 'shorts_target_seconds': 30},
            'production': {'editorial': assignment(self.config(), 0)}})
        prompt = _build_prompt(cfg, 'short', 'Everyday origin in India')
        self.assertIn('exactly 5 segments', prompt)
        self.assertIn('You Use It Every Day', prompt)
        self.assertIn('Follow for', prompt)
        self.assertNotIn('MUST be a real opinion/verdict question', prompt)
        self.assertNotIn('YouTube subscribers', prompt)

    @patch('src.script_generator.performance_examples')
    @patch('src.script_generator.recent_titles', return_value=[])
    def test_facebook_profile_does_not_use_youtube_winners_as_evidence(self, _, feedback):
        cfg = Config({'script': {'story_profile': 'evergreen_story'},
                      'production': {'editorial': assignment(self.config(), 0)}})
        prompt = _build_prompt(cfg, 'short', 'An origin story')
        feedback.assert_not_called()
        self.assertNotIn("TODAY'S DATE", prompt)

    def test_concise_structure_supports_one_fact_and_complete_payoff(self):
        beats = ['hook', 'setup', 'pivot', 'fact', 'callback']
        text = ['This familiar object has an unexpected history.', 'It originated in India.',
                'Its purpose changed over time.', 'Records explain the change.',
                'That history explains why the object looks familiar today.']
        self.assertIsNone(validate_structure(beats, text, 'evergreen_concise'))
        self.assertIsNotNone(validate_structure(beats[:-1], text[:-1], 'evergreen_concise'))
