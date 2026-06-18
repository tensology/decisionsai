"""
LLMs routes — /llms, /llms/*, /ollama/*
"""
import asyncio
import logging
from fastapi.responses import JSONResponse

from ._shared import OllamaPullRequest, LLMSettings, route_handler

logger = logging.getLogger(__name__)


def _is_available_model_entry(model) -> bool:
    """Return True unless the provider marks this model/endpoint unavailable."""
    if not isinstance(model, dict):
        return True

    explicit_false_flags = (
        model.get("available"),
        model.get("is_available"),
        model.get("isAvailable"),
        model.get("enabled"),
        model.get("is_enabled"),
    )
    if any(flag is False for flag in explicit_false_flags):
        return False

    status = str(model.get("status") or "").strip().lower()
    if status in {"unavailable", "disabled", "inactive", "offline", "deprecated"}:
        return False

    availability = str(model.get("availability") or "").strip().lower()
    if availability in {"unavailable", "disabled", "inactive", "offline"}:
        return False

    return True


def register_routes(router, templates):
    _STT_CHOICES = {
        "vosk": "Vosk (Local & Offline)",
        "whisper": "Whisper.cpp (Local & Offline)",
        "assemblyai": "AssemblyAI (universal-2)",
        "openai_whisper": "OpenAI Whisper (whisper-1)",
    }

    def _stt_short_from_full(raw: str) -> str:
        raw = (raw or "Whisper.cpp (Local & Offline)").strip()
        stt_map_to_short = {
            "vosk (local & offline)": "vosk",
            "whisper.cpp (local & offline)": "whisper",
            "assemblyai (universal-2)": "assemblyai",
            "assemblyai (nano)": "assemblyai",
            "assemblyai (best)": "assemblyai",
            "openai whisper (whisper-1)": "openai_whisper",
        }
        lowered = raw.lower()
        return stt_map_to_short.get(
            lowered,
            "whisper" if "whisper" in lowered
            else "vosk" if "vosk" in lowered
            else "openai_whisper" if "openai" in lowered
            else "assemblyai" if "assemblyai" in lowered
            else "whisper",
        )

    def _stt_option_available(settings: dict, stt_id: str) -> tuple[bool, str]:
        if stt_id == "assemblyai":
            if settings.get("assemblyai_enabled") and (settings.get("assemblyai_key") or "").strip():
                return True, ""
            return False, "AssemblyAI needs an enabled API key in API Keys."
        if stt_id == "openai_whisper":
            if settings.get("openai_enabled") and (settings.get("openai_key") or "").strip():
                return True, ""
            return False, "OpenAI Whisper needs an enabled OpenAI API key in API Keys."
        return True, ""

    def _available_stt_options(settings: dict) -> tuple[list[dict], dict]:
        options = []
        hidden = {}
        for stt_id, label in _STT_CHOICES.items():
            available, reason = _stt_option_available(settings, stt_id)
            item = {"id": stt_id, "name": label, "available": available, "reason": reason}
            if available:
                options.append(item)
            else:
                hidden[stt_id] = item
        return options, hidden

    def _fallback_stt_choice(options: list[dict]) -> str:
        preferred = ("whisper", "vosk")
        ids = {o["id"] for o in options}
        for stt_id in preferred:
            if stt_id in ids:
                return stt_id
        return options[0]["id"] if options else "whisper"

    @router.get("/llms")
    @route_handler("load LLMs settings")
    async def get_llms_settings():
        """Get current LLMs settings from DB (conversational_llm_*, coding_llm_*, vision_llm_*, image_llm_*)."""
        from distr.core.settings import load_settings_from_db
        from distr.core.agent.constants import DEFAULT_OLLAMA_MODELS_BY_TYPE
        settings = load_settings_from_db()

        def _provider(val):
            return (val or "ollama").strip().lower()

        def _model(val, llm_type=None):
            m = (val or "").strip()
            if not m and llm_type:
                m = DEFAULT_OLLAMA_MODELS_BY_TYPE.get(llm_type, "")
            return m

        _raw_stt = (settings.get("transcription_model") or "Whisper.cpp (Local & Offline)").strip()
        _stt_short = _stt_short_from_full(_raw_stt)
        _stt_options, _hidden_stt_options = _available_stt_options(settings)
        _stt_unavailable = _hidden_stt_options.get(_stt_short)
        if _stt_unavailable:
            _stt_short = _fallback_stt_choice(_stt_options)

        return JSONResponse({
            "stt_model": _stt_short,
            "stt_options": _stt_options,
            "stt_unavailable": _stt_unavailable,
            "conversational_provider": _provider(settings.get("conversational_llm_provider")),
            "conversational_model": _model(settings.get("conversational_llm_model"), "conversational"),
            "coding_provider": _provider(settings.get("coding_llm_provider")),
            "coding_model": _model(settings.get("coding_llm_model"), "coding"),
            "vision_provider": _provider(settings.get("vision_llm_provider")),
            "vision_model": _model(settings.get("vision_llm_model"), "vision"),
            "image_provider": _provider(settings.get("image_llm_provider")),
            "image_model": _model(settings.get("image_llm_model"), "image"),
            "video_provider": _provider(settings.get("video_llm_provider")),
            "video_model": _model(settings.get("video_llm_model"), "video"),
            "workflow_provider": ((settings.get("workflow_llm_provider") or "").strip().lower() or ""),
            "workflow_model": (settings.get("workflow_llm_model") or "").strip(),
            "computer_use_provider": (settings.get("computer_use_provider") or "").strip().lower() or "",
            "computer_use_model": (settings.get("computer_use_model") or "").strip(),
            "project_cli_low_backend": (settings.get("project_cli_low_backend") or "cursor").strip().lower(),
            "project_cli_low_model": (settings.get("project_cli_low_model") or "auto").strip(),
            "project_cli_medium_backend": (settings.get("project_cli_medium_backend") or "codex").strip().lower(),
            "project_cli_medium_model": (settings.get("project_cli_medium_model") or "auto").strip(),
            "project_cli_high_backend": (settings.get("project_cli_high_backend") or "codex").strip().lower(),
            "project_cli_high_model": (settings.get("project_cli_high_model") or "gpt-5.3-codex").strip(),
            "project_cli_low_codex_intelligence": (settings.get("project_cli_low_codex_intelligence") or "").strip(),
            "project_cli_low_codex_speed": (settings.get("project_cli_low_codex_speed") or "").strip(),
            "project_cli_medium_codex_intelligence": (settings.get("project_cli_medium_codex_intelligence") or "").strip(),
            "project_cli_medium_codex_speed": (settings.get("project_cli_medium_codex_speed") or "").strip(),
            "project_cli_high_codex_intelligence": (settings.get("project_cli_high_codex_intelligence") or "").strip(),
            "project_cli_high_codex_speed": (settings.get("project_cli_high_codex_speed") or "").strip(),
            "instant_dictation": settings.get("instant_dictation", True),
        })

    @router.post("/llms")
    @route_handler("save LLMs settings")
    async def save_llms_settings(settings_data: LLMSettings):
        """Save LLMs settings to DB."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db

        settings = load_settings_from_db()

        def _agent_llm_settings_fingerprint(s: dict) -> tuple:
            """Stable tuple of all LLMs-tab fields that affect the running agent or tools."""
            return (
                (s.get("transcription_model") or "").strip(),
                (s.get("conversational_llm_provider") or "ollama").strip().lower(),
                (s.get("conversational_llm_model") or "").strip(),
                (s.get("coding_llm_provider") or "ollama").strip().lower(),
                (s.get("coding_llm_model") or "").strip(),
                (s.get("vision_llm_provider") or "ollama").strip().lower(),
                (s.get("vision_llm_model") or "").strip(),
                (s.get("image_llm_provider") or "ollama").strip().lower(),
                (s.get("image_llm_model") or "").strip(),
                (s.get("video_llm_provider") or "").strip().lower(),
                (s.get("video_llm_model") or "").strip(),
                (s.get("workflow_llm_provider") or "").strip().lower(),
                (s.get("workflow_llm_model") or "").strip(),
                (s.get("computer_use_provider") or "").strip().lower(),
                (s.get("computer_use_model") or "").strip(),
                (s.get("project_cli_low_backend") or "").strip().lower(),
                (s.get("project_cli_low_model") or "").strip(),
                (s.get("project_cli_medium_backend") or "").strip().lower(),
                (s.get("project_cli_medium_model") or "").strip(),
                (s.get("project_cli_high_backend") or "").strip().lower(),
                (s.get("project_cli_high_model") or "").strip(),
            )

        _fp_before = _agent_llm_settings_fingerprint(settings)

        _stt = (settings_data.stt_model or "whisper").strip().lower()
        _stt_options, _hidden_stt_options = _available_stt_options(settings)
        _stt_available_ids = {o["id"] for o in _stt_options}
        if _stt not in _STT_CHOICES:
            _stt = "whisper"
        if _stt not in _stt_available_ids:
            reason = (_hidden_stt_options.get(_stt) or {}).get("reason") or "This speech-to-text backend is not available."
            return JSONResponse({"detail": reason}, status_code=400)
        settings["transcription_model"] = _STT_CHOICES.get(_stt, "Whisper.cpp (Local & Offline)")
        settings["conversational_llm_provider"] = (settings_data.conversational_provider or "ollama").strip()
        settings["conversational_llm_model"] = (settings_data.conversational_model or "").strip()
        # Sync legacy fields
        settings["llm_provider"] = settings["conversational_llm_provider"]
        settings["llm_model"] = settings["conversational_llm_model"]
        settings["agent_provider"] = settings["conversational_llm_provider"]
        settings["agent_model"] = settings["conversational_llm_model"]
        settings["coding_llm_provider"] = (settings_data.coding_provider or "ollama").strip()
        settings["coding_llm_model"] = (settings_data.coding_model or "").strip()
        settings["vision_llm_provider"] = (settings_data.vision_provider or "ollama").strip()
        settings["vision_llm_model"] = (settings_data.vision_model or "").strip()
        settings["image_llm_provider"] = (settings_data.image_provider or "ollama").strip()
        settings["image_llm_model"] = (settings_data.image_model or "").strip()
        settings["video_llm_provider"] = (settings_data.video_provider or "").strip()
        settings["video_llm_model"] = (settings_data.video_model or "").strip()
        workflow_provider = (settings_data.workflow_provider or "").strip()
        workflow_model = (settings_data.workflow_model or "").strip()
        settings["workflow_llm_provider"] = workflow_provider
        settings["workflow_llm_model"] = workflow_model
        settings["computer_use_provider"] = (settings_data.computer_use_provider or "").strip()
        settings["computer_use_model"] = (settings_data.computer_use_model or "").strip()
        from distr.core.kanban.codex_prefs import normalize_codex_intelligence, normalize_codex_speed
        from distr.core.project_cli_backends import normalize_backend_id
        settings["project_cli_low_backend"] = normalize_backend_id(settings_data.project_cli_low_backend or "cursor")
        settings["project_cli_low_model"] = (settings_data.project_cli_low_model or "auto").strip()
        settings["project_cli_medium_backend"] = normalize_backend_id(settings_data.project_cli_medium_backend or "codex")
        settings["project_cli_medium_model"] = (settings_data.project_cli_medium_model or "auto").strip()
        settings["project_cli_high_backend"] = normalize_backend_id(settings_data.project_cli_high_backend or "codex")
        settings["project_cli_high_model"] = (settings_data.project_cli_high_model or "gpt-5.3-codex").strip()
        settings["project_cli_low_codex_intelligence"] = normalize_codex_intelligence(
            settings_data.project_cli_low_codex_intelligence
        )
        settings["project_cli_low_codex_speed"] = normalize_codex_speed(settings_data.project_cli_low_codex_speed)
        settings["project_cli_medium_codex_intelligence"] = normalize_codex_intelligence(
            settings_data.project_cli_medium_codex_intelligence
        )
        settings["project_cli_medium_codex_speed"] = normalize_codex_speed(
            settings_data.project_cli_medium_codex_speed
        )
        settings["project_cli_high_codex_intelligence"] = normalize_codex_intelligence(
            settings_data.project_cli_high_codex_intelligence
        )
        settings["project_cli_high_codex_speed"] = normalize_codex_speed(settings_data.project_cli_high_codex_speed)
        settings["instant_dictation"] = bool(settings_data.instant_dictation)

        save_settings_to_db(settings)

        # Hot-swap live agent from web UI (FastAPI thread): marshal onto Qt main thread + agent commands.
        from distr.core.services.settings_service import (
            notify_conversational_llm_saved_for_running_agent,
            notify_stt_model_saved_for_running_agent,
        )

        notify_stt_model_saved_for_running_agent(settings.get("transcription_model") or "")

        # Hot-swap / recalibrate the live agent when *any* LLMs-tab field changed — not only
        # conversational provider/model. Vision/coding/kanban/etc. are read from DB in many
        # tools, but the main session.settings and pipeline still need a refresh so the
        # orchestrator matches what the user just saved.
        _fp_after = _agent_llm_settings_fingerprint(settings)
        if _fp_before != _fp_after:
            chat_id = settings.get("agent_current_chat_id") or settings.get("last_chat_id")
            notify_conversational_llm_saved_for_running_agent(
                (settings.get("conversational_llm_provider") or "ollama").strip(),
                (settings.get("conversational_llm_model") or "").strip(),
                chat_id,
            )

        return JSONResponse({"success": True, "message": "LLMs settings saved"})

    @router.get("/llms/available-providers")
    @route_handler("get available LLM providers", fallback={"providers": [{"id": "ollama", "name": "Ollama"}]})
    async def get_available_llm_providers():
        """Return only LLM providers that are configured (enabled + API key). Ollama always available."""
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        providers = [{"id": "ollama", "name": "Ollama"}]
        _provider_checks = [
            ("openai", "OpenAI", "openai_enabled", "openai_key"),
            ("anthropic", "Anthropic", "anthropic_enabled", "anthropic_key"),
            ("groq", "Groq", "groq_enabled", "groq_key"),
            ("openrouter", "OpenRouter", "openrouter_enabled", "openrouter_key"),
            ("kilocode", "KiloCode", "kilo_enabled", "kilo_key"),
            ("gemini", "Google Gemini", "gemini_enabled", "gemini_key"),
            ("nvidia", "NVIDIA", "nvidia_enabled", "nvidia_key"),
        ]
        for pid, pname, enabled_key, key_key in _provider_checks:
            from distr.core.services.settings_service import thirdparty_llm_provider_ready
            if thirdparty_llm_provider_ready(settings, enabled_key, key_key):
                providers.append({"id": pid, "name": pname})
        return JSONResponse({"providers": providers})

    @router.get("/llms/available-media-providers")
    @route_handler("get available media providers", fallback={"providers": []})
    async def get_available_media_providers():
        """Return media-only providers (Pixazo) when configured."""
        from distr.core.services.settings_service import thirdparty_llm_provider_ready
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        providers = []
        if thirdparty_llm_provider_ready(settings, "pixazo_enabled", "pixazo_key"):
            providers.append({"id": "pixazo", "name": "Pixazo"})
        return JSONResponse({"providers": providers})

    @router.get("/llms/models")
    @route_handler("get models", fallback={"models": []})
    async def get_llm_models(type: str, provider: str):
        """Get available models for a specific LLM type and provider."""
        from distr.core.chat import provider_slug
        from distr.core.settings import load_settings_from_db
        from distr.gui.utils.get_ollama_models import (
            get_installed_ollama_models,
            get_openai_models,
            get_anthropic_models,
            get_groq_models,
            get_kilo_models,
            get_gemini_models,
            get_nvidia_models,
        )
        from distr.gui.utils.get_pixazo_models import get_pixazo_models
        from distr.gui.utils.get_openrouter_models import get_openrouter_models

        settings = load_settings_from_db()
        models = []
        provider_key = provider_slug(provider)

        _fetchers = {
            "ollama": lambda: get_installed_ollama_models(),
            "openai": lambda: get_openai_models((settings.get("openai_key") or "").strip())
                if settings.get("openai_enabled") and (settings.get("openai_key") or "").strip() else [],
            "anthropic": lambda: get_anthropic_models((settings.get("anthropic_key") or "").strip())
                if settings.get("anthropic_enabled") and (settings.get("anthropic_key") or "").strip() else [],
            "groq": lambda: get_groq_models((settings.get("groq_key") or "").strip())
                if settings.get("groq_enabled") and (settings.get("groq_key") or "").strip() else [],
            "openrouter": lambda: get_openrouter_models((settings.get("openrouter_key") or "").strip())
                if settings.get("openrouter_enabled") and (settings.get("openrouter_key") or "").strip() else [],
            "kilocode": lambda: get_kilo_models((settings.get("kilo_key") or "").strip())
                if settings.get("kilo_enabled") and (settings.get("kilo_key") or "").strip() else [],
            "gemini": lambda: get_gemini_models((settings.get("gemini_key") or "").strip())
                if settings.get("gemini_enabled") and (settings.get("gemini_key") or "").strip() else [],
            "nvidia": lambda: get_nvidia_models((settings.get("nvidia_key") or "").strip())
                if settings.get("nvidia_enabled") and (settings.get("nvidia_key") or "").strip() else [],
            "pixazo": lambda: get_pixazo_models("image" if type == "image" else "video" if type == "video" else None)
                if settings.get("pixazo_enabled") and (settings.get("pixazo_key") or "").strip() else [],
        }
        fetcher = _fetchers.get(provider_key)
        if fetcher:
            models = fetcher()
        elif provider_key:
            logger.warning("No model fetcher for provider=%r (slug=%r)", provider, provider_key)

        # Providers may return endpoint/model rows that exist but are not currently
        # available. Hide those entries from all UI dropdowns.
        models = [m for m in models if _is_available_model_entry(m)]

        if models:
            def _is_dict(m):
                return isinstance(m, dict)

            # Filter by type capability
            _type_filters = {
                "conversational": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider_key == "ollama")),
                "coding": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider_key == "ollama")),
                "vision": lambda m: not _is_dict(m) or "image" in (m.get("input_modalities") or []),
                "image": lambda m: not _is_dict(m) or "image" in (m.get("output_modalities") or []),
                "video": lambda m: not _is_dict(m) or "video" in (m.get("output_modalities") or []),
                "workflow": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider_key == "ollama")),
                "step_runner": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider_key == "ollama")),  # legacy alias
                "computer_use": lambda m: True,
                "kanban": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider_key == "ollama")),
            }
            filt = _type_filters.get(type)
            if filt:
                filtered = [m for m in models if filt(m)]
                if filtered:
                    models = filtered

            # Clean display names and attach context window metadata for UI.
            from distr.core.services.context_window import context_window_for_model

            for m in models:
                if _is_dict(m) and "name" in m:
                    m["name"] = m["name"].replace(" (tools)", "")
                    while "(free) (free)" in m["name"]:
                        m["name"] = m["name"].replace("(free) (free)", "(free)")
                if _is_dict(m):
                    model_id = (m.get("id") or m.get("name") or "").strip()
                    if model_id and not m.get("context_window"):
                        m["context_window"] = context_window_for_model(provider_key, model_id)

        if not models and provider_key:
            logger.warning(
                "No %s models returned for provider=%r (slug=%r)",
                type,
                provider,
                provider_key,
            )

        return JSONResponse({"models": models})

    @router.get("/llms/recommendations")
    @route_handler("load model recommendations", fallback={"providers": {}, "last_updated": None})
    async def get_model_recommendations(provider: str = None):
        """Return cached model recommendations, optionally filtered by provider."""
        from distr.core.services.model_recommendations import load_recommendations
        data = load_recommendations(provider)
        return JSONResponse(data)

    @router.post("/llms/recommendations/refresh")
    @route_handler("start recommendations refresh")
    async def refresh_model_recommendations():
        """Trigger background agent to regenerate recommendations."""
        import threading
        from distr.core.services.model_recommendations import (
            refresh_recommendations, _refresh_running
        )
        if _refresh_running:
            return JSONResponse({"status": "already_running"}, status_code=409)
        threading.Thread(target=refresh_recommendations, daemon=True).start()
        return JSONResponse({"status": "started"})

    @router.get("/ollama/library")
    @route_handler("get Ollama library", fallback={"models": []})
    async def get_ollama_library():
        """Return available Ollama models from library with sizes and installed status."""
        from distr.gui.utils.get_ollama_models import get_available_ollama_models_with_sizes
        models = get_available_ollama_models_with_sizes()
        return JSONResponse({"models": models})

    @router.post("/ollama/refresh-library")
    @route_handler("refresh Ollama library")
    async def refresh_ollama_library():
        """Refresh Ollama library cache (scrape ollama.com)."""
        from distr.gui.utils.get_ollama_models import scrape_ollama_library
        scrape_ollama_library()
        return JSONResponse({"success": True, "message": "Library refreshed"})

    @router.post("/ollama/pull")
    @route_handler("pull Ollama model")
    async def pull_ollama_model_route(body: OllamaPullRequest):
        """Pull/download an Ollama model."""
        from distr.gui.utils.get_ollama_models import pull_ollama_model
        model_name = (body.model or "").strip()
        if not model_name:
            return JSONResponse({"success": False, "message": "Model name required"})
        size = (body.size or "").strip() or None
        success, message = await asyncio.to_thread(pull_ollama_model, model_name, size)
        return JSONResponse({"success": success, "message": message})
