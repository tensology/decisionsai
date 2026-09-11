import unittest

from task import normalize_tags


class NormalizeTagsTests(unittest.TestCase):
    def test_trims_and_lowercases(self):
        self.assertEqual(normalize_tags(["  Python ", "AI"]), ["python", "ai"])

    def test_ignores_blank_values(self):
        self.assertEqual(normalize_tags(["", "  ", "Code"]), ["code"])

    def test_deduplicates_after_normalizing(self):
        self.assertEqual(normalize_tags(["AI", " ai ", "ML", "ml"]), ["ai", "ml"])

    def test_preserves_first_seen_order(self):
        self.assertEqual(normalize_tags(["Beta", "alpha", "BETA"]), ["beta", "alpha"])


if __name__ == "__main__":
    unittest.main()
