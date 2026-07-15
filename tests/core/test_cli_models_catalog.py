"""CLI model catalog — Pi/OpenCode shared provider lists."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from distr.core.project_cli_backends import models_catalog as catalog


class CliModelsCatalogTests(unittest.TestCase):
    def test_pi_cli_models_merges_json_and_settings_catalog(self):
        fake_settings = {
            "nvidia_enabled": True,
            "nvidia_key": "nvapi-test",
        }
        with patch.object(catalog, "models_from_pi_json", return_value=[
            {"id": "qwen3:8b", "name": "Qwen3 8B", "provider": "ollama"},
        ]):
            with patch.object(catalog, "settings_backed_cloud_models", return_value=[
                {"id": "nvidia/nemotron-3-nano-30b-a3b", "name": "Nemotron Nano", "provider": "nvidia"},
            ]):
                models = catalog.pi_cli_models(fake_settings)
        ids = [m["id"] for m in models]
        self.assertIn("qwen3:8b", ids)
        self.assertIn("nvidia/nemotron-3-nano-30b-a3b", ids)

    def test_opencode_backend_registered(self):
        from distr.core.project_cli_backends import get_backend, normalize_backend_id

        self.assertEqual(normalize_backend_id("open-code"), "opencode")
        self.assertEqual(get_backend("opencode").id, "opencode")

    def test_recommender_prefers_stable_kilo_free_alias_over_expiring_promotion(self):
        selected = catalog.recommend_cli_model(
            [
                catalog.model_entry(
                    "bytedance-seed/dola-seed-2.0-pro:free",
                    "kilocode",
                    free=True,
                    tier="high",
                    scope="scoped",
                ),
                catalog.model_entry(
                    "openrouter/free",
                    "kilocode",
                    free=True,
                    tier="standard",
                    scope="scoped",
                ),
            ],
            prefer_free=True,
            prefer_local=False,
            prefer_scoped=True,
            complexity="high",
        )
        self.assertEqual(selected["id"], "openrouter/free")
        self.assertEqual(selected["provider"], "kilocode")


if __name__ == "__main__":
    unittest.main()
