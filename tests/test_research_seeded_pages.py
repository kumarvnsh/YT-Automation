import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import research
from src.config import Config


def _response(blocks):
    usage = SimpleNamespace(input_tokens=1, output_tokens=1,
                            server_tool_use=SimpleNamespace(web_search_requests=1))
    return SimpleNamespace(stop_reason="end_turn", usage=usage, content=blocks)


TEXT = SimpleNamespace(type="text", text='{"sources": [], "claims": []}', citations=[])


class ResearchSeededPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        patcher = patch("src.research._client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(research._step.update, pages=[])

    def test_research_seeded_pages_reach_prompt_and_citations(self) -> None:
        # Arrange
        page = {"type": "retrieved_page", "url": "https://loc.gov/x", "cited_text": "page text"}
        with patch("src.research._fetch_page", return_value=page):
            research.begin_step(Config({}), ["https://loc.gov/x"])
        self.client.messages.create.return_value = _response([TEXT])
        # Act
        _, citations = research._retrieve(Config({}), "topic")
        # Assert
        prompt = self.client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("page text", prompt)
        self.assertIn(page, citations)

    def test_research_max_uses_exceeded_keeps_answer(self) -> None:
        # Arrange: the model asked for one search too many; the answer still stands.
        capped = SimpleNamespace(type="web_search_tool_result",
                                 content={"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"})
        self.client.messages.create.return_value = _response([capped, TEXT])
        # Act
        packet, _ = research._retrieve(Config({}), "topic")
        # Assert
        self.assertEqual({"sources": [], "claims": []}, packet)


if __name__ == "__main__":
    unittest.main()
