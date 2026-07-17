from distr.core.project_cli_backends.timing import (
    model_parameter_billions,
    ollama_model_loaded,
    resolve_worker_timing,
)


def test_parameter_size_is_detected_from_local_model_name():
    assert model_parameter_billions("ornith:35b") == 35
    assert model_parameter_billions("qwen2.5-coder:7b") == 7
    assert model_parameter_billions("openrouter/free") is None


def test_cold_35b_local_model_gets_startup_and_execution_allowance():
    policy = resolve_worker_timing(
        backend_id="pi",
        model="ornith:35b",
        provider="ollama",
        complexity="medium",
        configured_timeout_seconds=300,
        model_loaded=False,
    )

    assert policy.timeout_seconds == 1800
    assert "35B" in policy.rationale
    assert "cold/not resident" in policy.rationale


def test_warm_local_model_has_smaller_but_nontrivial_safety_ceiling():
    policy = resolve_worker_timing(
        backend_id="pi",
        model="ornith:35b",
        provider="ollama",
        complexity="medium",
        configured_timeout_seconds=300,
        model_loaded=True,
    )

    assert policy.timeout_seconds == 1200
    assert "warm/resident" in policy.rationale


def test_high_complexity_and_explicit_larger_timeout_are_honoured():
    high = resolve_worker_timing(
        backend_id="pi",
        model="ornith:35b",
        provider="ollama",
        complexity="high",
        configured_timeout_seconds=300,
        model_loaded=False,
    )
    explicit = resolve_worker_timing(
        backend_id="pi",
        model="ornith:9b",
        provider="ollama",
        configured_timeout_seconds=2400,
        model_loaded=True,
    )

    assert high.timeout_seconds == 2250
    assert explicit.timeout_seconds == 2400


def test_remote_models_do_not_receive_local_cold_start_multiplier():
    policy = resolve_worker_timing(
        backend_id="pi",
        model="tencent/hy3-preview",
        provider="openrouter",
        configured_timeout_seconds=300,
        model_loaded=False,
    )

    assert policy.timeout_seconds == 900
    assert "cloud/remote" in policy.rationale


def test_ollama_residency_does_not_confuse_different_size_tags(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"models":[{"name":"ornith:9b"}]}'

    monkeypatch.setattr("distr.core.project_cli_backends.timing.urlopen", lambda *_args, **_kwargs: Response())

    assert ollama_model_loaded("ornith:9b") is True
    assert ollama_model_loaded("ornith:35b") is False
