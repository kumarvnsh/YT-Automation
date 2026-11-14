"""Regression checks for token-truncated cinematic scene generation."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from src.claude_motion import _request_scenes
from src.config import Config


def response(indices, stop='end_turn'):
    return NS(stop_reason=stop, content=[NS(type='text', text=''.join(
        f'<scene index="{i}"><html>scene {i}</html></scene>' for i in indices))],
        usage=NS(input_tokens=8408, output_tokens=30000))


class CinematicCompletionTests(unittest.TestCase):
    def generate(self, replies):
        client = MagicMock()
        client.beta.messages.stream.return_value.__enter__.return_value.get_final_message.side_effect = replies
        seg = NS(narration='History', keywords=[], beat='hook')
        with patch('anthropic.Anthropic', return_value=client):
            result = _request_scenes(Config({'video': {'claude_motion': {'style': 'cinematic_2d'}}}),
                                     'History', [seg] * 7, [(i, i + 1) for i in range(7)], 540, 960)
        return result, client.beta.messages.stream

    def test_recovers_five_missing_scenes_individually_preserving_originals(self):
        result, stream = self.generate([response([0, 1], 'max_tokens')] +
                                       [response([i, 0]) for i in range(2, 7)])
        self.assertEqual(set(result), set(range(7)))
        self.assertEqual(stream.call_count, 6)
        for index, call in enumerate(stream.call_args_list[1:], 2):
            self.assertIn(f'ONLY these missing zero-based scene IDs: [{index}]',
                          call.kwargs['messages'][0]['content'])
        self.assertEqual(result[0], '<html>scene 0</html>')

    def test_missing_scene_failure_is_bounded_and_names_scene(self):
        with self.assertRaisesRegex(RuntimeError, r'scene 2.*max_tokens'):
            self.generate([response([0, 1], 'max_tokens'), response([], 'max_tokens')])

    def test_complete_generation_needs_no_recovery(self):
        result, stream = self.generate([response(range(7))])
        self.assertEqual(len(result), 7)
        self.assertEqual(stream.call_count, 1)
