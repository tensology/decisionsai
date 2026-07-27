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
            with patch.object(catalog, "installed_ollama_cli_models", return_value=[]):
                with patch.object(catalog, "settings_backed_cloud_models", return_value=[
                    {"id": "nvidia/nemotron-3-nano-30b-a3b", "name": "Nemotron Nano", "provider": "nvidia"},
                ]):
                    models = catalog.pi_cli_models(fake_settings)
        ids = [m["id"] for m in models]
        self.assertIn("qwen3:8b", ids)
        self.assertIn("nvidia/nemotron-3-nano-30b-a3b", ids)

    def test_installed_ollama_models_preserve_local_and_cloud_scope(self):
        installed = [
            {"id": "ornith:9b", "name": "Ornith 9B", "local": True},
            {"id": "kimi-k2.7-code:cloud", "name": "Kimi Cloud", "local": False},
        ]
        with patch(
            "distr.gui.utils.get_ollama_models.get_installed_ollama_models",
            return_value=installed,
        ), patch.object(catalog, "_ollama_model_chat_ready", return_value=True):
            models = catalog.installed_ollama_cli_models({"ollama_enabled": True})

        by_id = {model["id"]: catalog.enrich_model_entry(model) for model in models}
        self.assertTrue(by_id["ornith:9b"]["local"])
        self.assertTrue(by_id["ornith:9b"]["free"])
        self.assertFalse(by_id["kimi-k2.7-code:cloud"]["local"])
        self.assertFalse(by_id["kimi-k2.7-code:cloud"]["free"])

    def test_pi_manifest_ollama_cloud_alias_is_not_mislabeled_local_or_free(self):
        model = catalog.enrich_model_entry(
            catalog.model_entry(
                "qwen3.5:397b-cloud",
                "ollama",
                scope="scoped",
            )
        )

        self.assertFalse(model["local"])
        self.assertFalse(model["free"])

    def test_pi_manifest_skips_stale_local_ollama_but_keeps_cloud_alias(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import json

        with TemporaryDirectory() as folder:
            manifest = Path(folder) / "models.json"
            manifest.write_text(json.dumps({
                "providers": {
                    "ollama": {
                        "models": [
                            {"id": "stale:7b"},
                            {"id": "qwen3.5:397b-cloud"},
                        ]
                    }
                }
            }), encoding="utf-8")
            with patch.object(catalog.os.path, "expanduser", return_value=str(manifest)), patch.object(
                catalog,
                "_ollama_model_chat_ready",
                side_effect=lambda model_id: model_id != "stale:7b",
            ):
                models = catalog.models_from_pi_json()

        by_id = {model["id"]: catalog.enrich_model_entry(model) for model in models}
        self.assertNotIn("stale:7b", by_id)
        self.assertFalse(by_id["qwen3.5:397b-cloud"]["local"])
        self.assertFalse(by_id["qwen3.5:397b-cloud"]["free"])

    def test_installed_ollama_catalog_skips_stale_local_manifest(self):
        installed = [
            {"id": "qwen3:8b", "name": "Qwen3 8B", "local": True},
            {"id": "ornith:9b", "name": "Ornith 9B", "local": True},
        ]
        with patch(
            "distr.gui.utils.get_ollama_models.get_installed_ollama_models",
            return_value=installed,
        ), patch.object(
            catalog,
            "_ollama_model_chat_ready",
            side_effect=lambda model_id: model_id == "ornith:9b",
        ):
            models = catalog.installed_ollama_cli_models({"ollama_enabled": True})

        self.assertEqual([model["id"] for model in models], ["ornith:9b"])

    def test_recommender_can_select_installed_ornith_for_local_policy(self):
        selected = catalog.recommend_cli_model(
            [
                catalog.model_entry("openrouter/free", "kilocode", free=True, scope="scoped"),
                {
                    **catalog.model_entry("ornith:9b", "ollama", free=True, scope="scoped"),
                    "local": True,
                },
            ],
            prefer_free=True,
            prefer_local=True,
            prefer_scoped=True,
            complexity="standard",
        )
        self.assertEqual(selected["id"], "ornith:9b")
        self.assertEqual(selected["provider"], "ollama")

    def test_recommender_prefers_large_local_model_for_high_complexity(self):
        selected = catalog.recommend_cli_model(
            [
                catalog.model_entry("codegemma:2b", "ollama", free=True, scope="scoped"),
                catalog.model_entry("ornith:35b", "ollama", free=True, scope="scoped"),
            ],
            prefer_free=True,
            prefer_local=True,
            prefer_scoped=True,
            complexity="high",
        )

        self.assertEqual(selected["id"], "ornith:35b")

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
                catalog.model_entry(
                    "kilo-auto/free",
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
        self.assertEqual(selected["id"], "kilo-auto/free")
        self.assertEqual(selected["provider"], "kilocode")


if __name__ == "__main__":
    unittest.main()
