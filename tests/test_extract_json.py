import unittest

from src.script_generator import _extract_json


class ExtractJsonTests(unittest.TestCase):
    def test_ignores_trailing_object_and_prose(self) -> None:
        raw = 'Here it is:\n{"title": "A", "n": {"x": 1}}\n\n{"note": "draft"} trailing'
        self.assertEqual({"title": "A", "n": {"x": 1}}, _extract_json(raw))

    def test_fenced_json(self) -> None:
        self.assertEqual({"a": 1}, _extract_json('```json\n{"a": 1}\n```'))


if __name__ == "__main__":
    unittest.main()
