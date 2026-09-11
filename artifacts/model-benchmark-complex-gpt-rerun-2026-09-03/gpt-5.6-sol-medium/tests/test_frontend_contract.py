import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = (ROOT / "web/api/tickets.js").read_text()
        cls.hook = (ROOT / "web/hooks/useTickets.js").read_text()
        cls.component = (ROOT / "web/components/TicketBoard.jsx").read_text()
        cls.template = (ROOT / "templates/board.html").read_text()

    def test_api_accepts_options_without_breaking_old_callers(self):
        self.assertRegex(self.api, r"fetchBoardTickets\s*\(\s*boardId\s*,\s*\{[^}]*includeArchived\s*=\s*false[^}]*\}\s*=\s*\{\s*\}")

    def test_api_uses_include_archived_query_name(self):
        self.assertIn("include_archived", self.api)

    def test_api_only_adds_true_value_when_enabled(self):
        self.assertRegex(self.api, r"if\s*\(\s*includeArchived\s*\)")
        self.assertRegex(self.api, r"include_archived[^\n]*(true|1)")

    def test_api_still_encodes_board_id(self):
        self.assertIn("encodeURIComponent(boardId)", self.api)

    def test_hook_owns_include_archived_state(self):
        self.assertRegex(self.hook, r"useState\s*\(\s*false\s*\)")
        self.assertIn("includeArchived", self.hook)

    def test_hook_passes_flag_to_api(self):
        self.assertRegex(self.hook, r"fetchBoardTickets\s*\(\s*boardId\s*,\s*\{\s*includeArchived\s*\}\s*\)")

    def test_hook_reload_tracks_flag_dependency(self):
        match = re.search(r"useCallback\s*\([\s\S]*?,\s*\[([^]]+)]\s*\);", self.hook)
        self.assertIsNotNone(match)
        self.assertIn("includeArchived", match.group(1))

    def test_hook_uses_idiomatic_callback_shape(self):
        self.assertNotRegex(self.hook, r"useCallback\s*\(\s*\[")

    def test_hook_returns_toggle_contract(self):
        self.assertIn("setIncludeArchived", self.hook)

    def test_component_renders_checkbox(self):
        self.assertRegex(self.component, r"type\s*=\s*[\"']checkbox[\"']")

    def test_component_checkbox_is_controlled(self):
        self.assertIn("checked={includeArchived}", self.component)
        self.assertIn("setIncludeArchived", self.component)

    def test_component_has_accessible_label(self):
        self.assertRegex(self.component.lower(), r"(show|include) archived")

    def test_component_keeps_refresh_action(self):
        self.assertIn("onClick={reload}", self.component)

    def test_template_exposes_archived_default(self):
        self.assertRegex(self.template, r"data-include-archived=[\"']false[\"']")


if __name__ == "__main__":
    unittest.main()
