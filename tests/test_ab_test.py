import unittest

from src.config import Config
from src.pipeline import ab_arm
from src.script_generator import title_style_error


class AbTestTests(unittest.TestCase):
    def cfg(self, enabled=True):
        return Config({'ab_test': {'enabled': enabled, 'id': 'voice-title-2x2', 'start_slot': 10, 'posts': 8}})

    def test_ab_arm_every_four_posts_cover_all_cells(self):
        # Arrange
        cfg = self.cfg()
        # Act
        cells = [(a['title_style'], a['voice']) for a in (ab_arm(cfg, n) for n in range(10, 14))]
        # Assert
        self.assertEqual(sorted(cells), sorted({('question', 'kokoro'), ('statement', 'kokoro'),
                                                ('question', 'edge'), ('statement', 'edge')}))

    def test_ab_arm_outside_window_or_disabled_is_none(self):
        self.assertIsNone(ab_arm(self.cfg(), 9))
        self.assertIsNone(ab_arm(self.cfg(), 18))
        self.assertIsNone(ab_arm(self.cfg(enabled=False), 10))
        self.assertIsNone(ab_arm(Config({}), 10))

    def test_title_style_error_flags_wrong_shape_only(self):
        self.assertIsNotNone(title_style_error('Why Did Rome Fall?', 'statement'))
        self.assertIsNone(title_style_error('Rome Fell — Then Rose Again', 'statement'))
        self.assertIsNotNone(title_style_error('Rome Fell — Then Rose Again', 'question'))
        self.assertIsNone(title_style_error('Did Rome Fall? | Myths You Learned in School', 'question'))
        self.assertIsNone(title_style_error('Anything', None))


if __name__ == '__main__':
    unittest.main()
