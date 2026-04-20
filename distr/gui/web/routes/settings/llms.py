"""
LLMs routes — /llms, /llms/*, /ollama/*
"""
import asyncio

from fastapi.responses import JSONResponse

from ._shared import logger, OllamaPullRequest, route_handler


def register_routes(router, templates):

    @router.get("/llms")
    @route_handler("load LLMs settings")
    async def get_llms_settings():
        """Get current LLMs settings from DB (conversational_llm_*, coding_llm_*, vision_llm_*, image_llm_*)."""
        from distr.core.settings import load_settings_from_db
        from distr.core.agent.constants import DEFAULT_MODELS, DEFAULT_OLLAMA_MODELS_BY_TYPE
        settings = load_settings_from_db()

        def _provider(val):
            return (val or "ollama").strip().lower()

        def _model(val, llm_type=None):
            m = (val or "").strip()
            if not m and llm_type:
                m = DEFAULT_OLLAMA_MODELS_BY_TYPE.get(llm_type, "")
            return m

        _raw_stt = (settings.get("transcription_model") or "Whisper.cpp (Local & Offline)").strip()
        _stt_map_to_short = {
            "vosk (local & offline)": "vosk",
            "whisper.cpp (local & offline)": "whisper",
            "assemblyai (universal-2)": "assemblyai",
            "assemblyai (nano)": "assemblyai",
            "assemblyai (best)": "assemblyai",
            "openai whisper (whisper-1)": "openai_whisper",
        }
        _stt_short = _stt_map_to_short.get(
            _raw_stt.lower(),
            "whisper" if "whisper" in _raw_stt.lower()
            else "vosk" if "vosk" in _raw_stt.lower()
            else "openai_whisper" if "openai" in _raw_stt.lower()
            else "assemblyai" if "assemblyai" in _raw_stt.lower()
            else "whisper"
        )

        return JSONResponse({
            "stt_model": _stt_short,
            "conversational_provider": _provider(settings.get("conversational_llm_provider")),
            "conversational_model": _model(settings.get("conversational_llm_model"), "conversational"),
            "coding_provider": _provider(settings.get("coding_llm_provider")),
            "coding_model": _model(settings.get("coding_llm_model"), "coding"),
            "vision_provider": _provider(settings.get("vision_llm_provider")),
            "vision_model": _model(settings.get("vision_llm_model"), "vision"),
            "image_provider": _provider(settings.get("image_llm_provider")),
            "image_model": _model(settings.get("image_llm_model"), "image"),
            "workflow_provider": ((settings.get("workflow_llm_provider") or "").strip().lower() or ""),
            "workflow_model": (settings.get("workflow_llm_model") or "").strip(),
            "computer_use_provider": (settings.get("computer_use_provider") or "").strip().lower() or "",
            "computer_use_model": (settings.get("computer_use_model") or "").strip(),
            "kanban_provider": (settings.get("kanban_agent_orchestrator_provider") or "").strip().lower() or "",
            "kanban_model": (settings.get("kanban_agent_orchestrator_model") or "").strip(),
        })

    @router.post("/llms")
    @route_handler("save LLMs settings")
    async def save_llms_settings(settings_data: dict):
        """Save LLMs settings to DB."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db

        settings = load_settings_from_db()

        _stt = (settings_data.get("stt_model") or "whisper").strip().lower()
        _stt_map_to_full = {
            "vosk": "Vosk (Local & Offline)",
            "whisper": "Whisper.cpp (Local & Offline)",
            "assemblyai": "AssemblyAI (universal-2)",
            "openai_whisper": "OpenAI Whisper (whisper-1)",
        }
        settings["transcription_model"] = _stt_map_to_full.get(_stt, "Whisper.cpp (Local & Offline)")
        settings["conversational_llm_provider"] = (settings_data.get("conversational_provider") or "ollama").strip()
        settings["conversational_llm_model"] = (settings_data.get("conversational_model") or "").strip()
        # Sync legacy fields
        settings["llm_provider"] = settings["conversational_llm_provider"]
        settings["llm_model"] = settings["conversational_llm_model"]
        settings["agent_provider"] = settings["conversational_llm_provider"]
        settings["agent_model"] = settings["conversational_llm_model"]
        settings["coding_llm_provider"] = (settings_data.get("coding_provider") or "ollama").strip()
        settings["coding_llm_model"] = (settings_data.get("coding_model") or "").strip()
        settings["vision_llm_provider"] = (settings_data.get("vision_provider") or "ollama").strip()
        settings["vision_llm_model"] = (settings_data.get("vision_model") or "").strip()
        settings["image_llm_provider"] = (settings_data.get("image_provider") or "ollama").strip()
        settings["image_llm_model"] = (settings_data.get("image_model") or "").strip()
        workflow_provider = (settings_data.get("workflow_provider") or "").strip()
        workflow_model = (settings_data.get("workflow_model") or "").strip()
        settings["workflow_llm_provider"] = workflow_provider
        settings["workflow_llm_model"] = workflow_model
        settings["computer_use_provider"] = (settings_data.get("computer_use_provider") or "").strip()
        settings["computer_use_model"] = (settings_data.get("computer_use_model") or "").strip()
        settings["kanban_agent_orchestrator_provider"] = (settings_data.get("kanban_provider") or "").strip()
        settings["kanban_agent_orchestrator_model"] = (settings_data.get("kanban_model") or "").strip()

        save_settings_to_db(settings)
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
        ]
        for pid, pname, enabled_key, key_key in _provider_checks:
            if settings.get(enabled_key) and (settings.get(key_key) or "").strip():
                providers.append({"id": pid, "name": pname})
        return JSONResponse({"providers": providers})

    @router.get("/llms/models")
    @route_handler("get models", fallback={"models": []})
    async def get_llm_models(type: str, provider: str):
        """Get available models for a specific LLM type and provider."""
        from distr.core.settings import load_settings_from_db
        from distr.gui.utils.get_ollama_models import (
            get_installed_ollama_models,
            get_openai_models,
            get_anthropic_models,
            get_groq_models,
            get_kilo_models,
            get_gemini_models,
        )
        from distr.gui.utils.get_openrouter_models import get_openrouter_models

        settings = load_settings_from_db()
        models = []

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
        }
        fetcher = _fetchers.get(provider)
        if fetcher:
            models = fetcher()

        if models:
            def _is_dict(m):
                return isinstance(m, dict)

            # Filter by type capability
            _type_filters = {
                "conversational": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider == "ollama")),
                "coding": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider == "ollama")),
                "vision": lambda m: not _is_dict(m) or "image" in (m.get("input_modalities") or []),
                "image": lambda m: not _is_dict(m) or "image" in (m.get("output_modalities") or []),
                "workflow": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider == "ollama")),
                "step_runner": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider == "ollama")),  # legacy alias
                "computer_use": lambda m: True,
                "kanban": lambda m: not _is_dict(m) or m.get("supports_tools", not (provider == "ollama")),
            }
            filt = _type_filters.get(type)
            if filt:
                filtered = [m for m in models if filt(m)]
                if filtered:
                    models = filtered

            # Clean display names
            for m in models:
                if _is_dict(m) and "name" in m:
                    m["name"] = m["name"].replace(" (tools)", "")
                    while "(free) (free)" in m["name"]:
                        m["name"] = m["name"].replace("(free) (free)", "(free)")

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
