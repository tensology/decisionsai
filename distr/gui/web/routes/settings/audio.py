"""
Audio routes — /audio, /audio/devices, /audio/detect, /audio/devices-version
"""
from fastapi.responses import JSONResponse

from ._shared import logger, AudioSettings, route_handler


def register_routes(router, templates):

    @router.get("/audio")
    @route_handler("load audio settings")
    async def get_audio_settings():
        """Get current audio settings"""
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        return JSONResponse({
            "input_device": settings.get("input_device", "System Default"),
            "output_device": settings.get("output_device", "System Default"),
            "remember_audio_settings": settings.get("lock_sound", False),
            "locked_output": settings.get("locked_output"),
            "locked_input": settings.get("locked_input"),
        })

    @router.post("/audio")
    @route_handler("save audio settings")
    async def save_audio_settings_route(settings_data: AudioSettings):
        """Save audio settings"""
        from distr.core.services.settings_service import save_audio_settings, _run_on_qt_main_thread
        save_audio_settings(settings_data)

        in_dev = settings_data.input_device
        out_dev = settings_data.output_device
        try:
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None and hasattr(app, "update_agent_audio_devices"):
                def _notify():
                    app.update_agent_audio_devices(in_dev, out_dev)

                _run_on_qt_main_thread(_notify, label="audio_devices_changed")
                logger.info("Scheduled agent audio update on main thread: input=%s, output=%s", in_dev, out_dev)
        except Exception as e:
            logger.debug("Could not schedule agent audio update from web (may be headless): %s", e)

        return JSONResponse({"success": True, "message": "Audio settings saved"})

    @router.get("/audio/devices")
    async def get_audio_devices():
        """Get available audio devices (fresh PortAudio names; id == name)."""
        try:
            from distr.core.audio.utils import query_native_devices, get_device_type, format_devices_for_api
            outputs, inputs = query_native_devices()
            if not outputs and not inputs:
                import sounddevice as sd
                devices = sd.query_devices()
                for dev in devices:
                    name = (dev.get("name") or "").strip()
                    if not name:
                        continue
                    di = {"name": name, "id": name, "type": get_device_type(name)}
                    if dev.get("max_output_channels", 0) > 0:
                        outputs.append(di)
                    if dev.get("max_input_channels", 0) > 0:
                        inputs.append(di)
            return JSONResponse({
                "input_devices": format_devices_for_api(inputs),
                "output_devices": format_devices_for_api(outputs),
            })
        except Exception as e:
            logger.error("Failed to get audio devices: %s", e, exc_info=True)
            try:
                from distr.core.audio.utils import get_device_type, format_devices_for_api
                import sounddevice as sd
                devices = sd.query_devices()
                input_devices = []
                output_devices = []
                for device in devices:
                    name = (device.get("name") or "").strip()
                    if not name:
                        continue
                    di = {"name": name, "id": name, "type": get_device_type(name)}
                    if device.get("max_input_channels", 0) > 0:
                        input_devices.append(di)
                    if device.get("max_output_channels", 0) > 0:
                        output_devices.append(di)
                return JSONResponse({
                    "input_devices": format_devices_for_api(input_devices),
                    "output_devices": format_devices_for_api(output_devices),
                })
            except Exception:
                return JSONResponse({
                    "input_devices": [{"name": "System Default", "id": -1}],
                    "output_devices": [{"name": "System Default", "id": -1}],
                })

    @router.post("/audio/detect")
    @route_handler("detect audio devices")
    async def detect_audio_devices():
        """Run full device detection and return updated device list."""
        from distr.core.audio.utils import detect_devices, format_devices_for_api
        from distr.gui.web.audio_events import increment_audio_devices_updated
        _, _, merged_outputs, merged_inputs = detect_devices()
        increment_audio_devices_updated()
        return JSONResponse({
            "input_devices": format_devices_for_api(merged_inputs),
            "output_devices": format_devices_for_api(merged_outputs),
            "success": True
        })

    @router.get("/audio/devices-version")
    @route_handler("get audio devices version", fallback={"version": 0})
    async def get_audio_devices_version():
        """Return a version counter that increments when new devices are detected."""
        from distr.gui.web.audio_events import get_audio_devices_update_counter
        return JSONResponse({"version": get_audio_devices_update_counter()})
