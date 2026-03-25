"""
Third-party routes — /thirdparty, /validate
"""
import asyncio

from fastapi.responses import JSONResponse

from ._shared import (
    logger,
    ThirdPartySettings,
    ValidateRequest,
    resolve_secret_update,
    redact_thirdparty_settings,
    route_handler,
)


def register_routes(router, templates):

    @router.get("/thirdparty")
    @route_handler("load third-party settings")
    async def get_thirdparty_settings():
        """Get current third-party provider settings"""
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        return JSONResponse(redact_thirdparty_settings(settings))

    @router.post("/thirdparty")
    @route_handler("save third-party settings")
    async def save_thirdparty_settings(settings_data: ThirdPartySettings):
        """Save third-party provider settings"""
        from distr.core.services.settings_service import save_thirdparty_settings as _save
        _save(settings_data, resolve_secret_update)
        return JSONResponse({"success": True, "message": "Settings saved successfully"})

    @router.post("/validate")
    @route_handler("validate API key", fallback={"valid": False, "error": "Validation failed"})
    async def validate_api_key(request: ValidateRequest):
        """Validate an API key for a provider"""
        from distr.core.api_validation import validate_provider

        loop = asyncio.get_event_loop()
        is_valid, error_msg = await loop.run_in_executor(
            None,
            validate_provider,
            request.provider,
            request.key
        )

        return JSONResponse({
            "valid": is_valid,
            "error": error_msg if not is_valid else ""
        })
