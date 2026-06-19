from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


SAMPLE_TERMINAL_BENCH_HTML = """
<html>
  <body>
    <script>
      self.__next_f.push([1,"payload:{\\"entries\\":[{\\"agent\\":\\"Codex CLI\\",\\"model\\":[\\"GPT-5.5\\"],\\"modelNames\\":[\\"gpt-5.5\\"],\\"modelProviders\\":[\\"openai\\"],\\"modelOrganization\\":[\\"OpenAI\\"],\\"date\\":\\"2026-05-14\\",\\"accuracy\\":0.822,\\"stderr\\":0.022,\\"verified\\":true,\\"key\\":\\"codex__gpt-5.5\\"},{\\"agent\\":\\"Capy\\",\\"model\\":[\\"GPT-5.5\\"],\\"modelNames\\":[\\"gpt-5.5\\"],\\"modelProviders\\":[\\"openai\\"],\\"modelOrganization\\":[\\"OpenAI\\"],\\"date\\":\\"2026-05-20\\",\\"accuracy\\":0.831,\\"stderr\\":0.021,\\"verified\\":true,\\"key\\":\\"capy__gpt-5.5\\"},{\\"agent\\":\\"Claude Code\\",\\"model\\":[\\"Claude Opus 4.6\\"],\\"modelNames\\":[\\"claude-opus-4.6\\"],\\"modelProviders\\":[\\"anthropic\\"],\\"modelOrganization\\":[\\"Anthropic\\"],\\"date\\":\\"2026-05-18\\",\\"accuracy\\":0.58,\\"stderr\\":0.029,\\"verified\\":true,\\"key\\":\\"claude-code__claude-opus-4.6\\"}],\\"className\\":\\"leaderboard\\",\\"name\\":\\"terminal-bench\\",\\"version\\":\\"2.0\\"}"]);
    </script>
  </body>
</html>
"""


def test_terminal_bench_parser_extracts_entries_from_embedded_payload():
    from distr.core.services import llm_benchmark_service

    entries = llm_benchmark_service.parse_terminal_bench_entries(SAMPLE_TERMINAL_BENCH_HTML)

    assert len(entries) == 3
    assert entries[0]["model"] == ["GPT-5.5"]
    assert entries[1]["accuracy"] == 0.831


def test_terminal_bench_aggregation_prefers_best_score_and_tracks_latest_date():
    from distr.core.services import llm_benchmark_service

    aggregated = llm_benchmark_service.aggregate_terminal_bench_models(
        llm_benchmark_service.parse_terminal_bench_entries(SAMPLE_TERMINAL_BENCH_HTML)
    )

    assert aggregated[0]["model_name"] == "GPT-5.5"
    assert aggregated[0]["provider"] == "openai"
    assert aggregated[0]["best_accuracy"] == 83.1
    assert aggregated[0]["submission_count"] == 2
    assert aggregated[0]["latest_date"] == "2026-05-20"


def test_llm_benchmark_endpoint_returns_selected_and_leaderboard_payload(monkeypatch):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"openai_enabled": True, "openai_key": "sk-test"},
    )
    monkeypatch.setattr(
        "distr.core.services.llm_benchmark_service.build_llm_benchmark_payload",
        lambda **kwargs: {
            "selected_model": {"id": "gpt-5.5", "label": "GPT-5.5"},
            "comparison_model": {"id": "claude-opus-4.6", "label": "Claude Opus 4.6"},
            "leaderboard": [{"id": "gpt-5.5", "label": "GPT-5.5", "performance_score": 83.1}],
            "sort": kwargs.get("sort"),
            "type": kwargs.get("llm_type"),
        },
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get(
        "/api/llms/benchmark",
        params={"type": "coding", "provider": "openai", "model": "gpt-5.5", "compare_model": "claude-opus-4.6", "sort": "value"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_model"]["id"] == "gpt-5.5"
    assert payload["comparison_model"]["id"] == "claude-opus-4.6"
    assert payload["sort"] == "value"
    assert payload["type"] == "coding"
