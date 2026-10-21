import unittest
from src import claude_motion as motion
from src.config import Config
from src.editorial import assignment

class ColorPaletteTests(unittest.TestCase):
    def test_odd_region_count_still_covers_both_durations(self):
        cfg=Config({'editorial': {'regions':[{'id':str(i)} for i in range(9)],
            'series':[{'id':str(i)} for i in range(3)],
            'duration_variants':[{'id':'concise','seconds':30},{'id':'story','seconds':50}]}})
        rows=[assignment(cfg,i) for i in range(54)]
        self.assertEqual(len({(r['region']['id'],r['series']['id'],r['duration_variant']) for r in rows}),54)

    def test_all_six_approved_palettes_are_available_and_injection_is_idempotent(self):
        self.assertEqual(set(motion.PALETTES), {'navy_teal','ocean_cyan','deep_teal_mint','blue_gold','teal_coral','ice_teal'})
        for name in motion.PALETTES:
            html=motion.apply_palette('<html><head></head><body>Sample</body></html>',name)
            self.assertIn('histold-palette',html)
            self.assertEqual(motion.apply_palette(html,name),html)

    def test_palette_text_and_accents_have_readable_background_contrast(self):
        def luminance(value):
            values=[int(value[i:i+2],16)/255 for i in (1,3,5)]
            values=[v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4 for v in values]
            return sum(v*w for v,w in zip(values,(0.2126,0.7152,0.0722)))
        for palette in motion.PALETTES.values():
            for color in ('parch','accent'):
                pair=sorted([luminance(palette['ink']),luminance(palette[color])])
                self.assertGreaterEqual((pair[1]+0.05)/(pair[0]+0.05),4.5)
