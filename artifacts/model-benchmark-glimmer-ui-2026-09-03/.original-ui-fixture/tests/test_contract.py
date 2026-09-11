import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class UIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html=(ROOT/'index.html').read_text()
        cls.css=(ROOT/'styles.css').read_text()
        cls.js=(ROOT/'app.js').read_text()

    def test_landmarks_and_dialog_remain(self):
        for token in ('<main', '<nav', '<aside', '<dialog'):
            self.assertIn(token, self.html)
    def test_search_has_programmatic_label(self):
        self.assertRegex(self.html, r'<label[^>]+for=["\']run-search')
    def test_live_regions_remain(self):
        self.assertGreaterEqual(self.html.count('aria-live='), 2)
    def test_interactions_remain(self):
        for token in ('data-filter', 'data-view', 'run-search', 'nav-toggle', 'cancel-dialog'):
            self.assertIn(token, self.js + self.html)
    def test_responsive_breakpoint_exists(self):
        self.assertRegex(self.css, r'@media\s*\([^)]*max-width')
    def test_focus_visible_is_authored(self):
        self.assertIn(':focus-visible', self.css)
    def test_reduced_motion_is_handled(self):
        self.assertIn('prefers-reduced-motion', self.css)
    def test_design_tokens_exist(self):
        self.assertGreaterEqual(len(re.findall(r'--[a-zA-Z][\w-]*\s*:', self.css)), 8)
    def test_no_remote_dependencies(self):
        self.assertNotRegex(self.html + self.css + self.js, r'https?://')
    def test_no_unicode_icon_substitutes(self):
        self.assertNotRegex(self.html, r'[😀-🙏]')

if __name__ == '__main__': unittest.main()
