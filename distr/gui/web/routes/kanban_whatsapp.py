"""WhatsApp-focused Kanban route registrations."""

from datetime import datetime
import asyncio
import base64
import json
import logging
import os
import tempfile
import subprocess
import shutil
from urllib.parse import quote

from fastapi import HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from distr.core.paths import DB_DIR
from distr.core.db import get_session, WhatsAppMessage
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, KanbanTicketFile
from distr.gui.web.security import is_allowed_local_origin

logger = logging.getLogger(__name__)

_wa_sse_queues = []
_wa_ws_clients = set()
_wa_sse_hooked = False
_wa_sse_loop = None


def register_whatsapp_routes(router, relay_auth_headers, load_or_create_device_identity):
    def _is_voice_type(media_type: str, media_mime_type: str) -> bool:
        t = str(media_type or "").lower()
        m = str(media_mime_type or "").lower()
        return t in ("voice", "audio", "ptt") or m.startswith("audio/")

    def _is_video_type(media_type: str, media_mime_type: str) -> bool:
        t = str(media_type or "").lower()
        m = str(media_mime_type or "").lower()
        return t == "video" or m.startswith("video/")

    def _is_image_type(media_type: str, media_mime_type: str) -> bool:
        t = str(media_type or "").lower()
        m = str(media_mime_type or "").lower()
        return t in ("photo", "image") or m.startswith("image/")

    def _upsert_extracted_block(existing_caption: str, label: str, extracted_text: str) -> str:
        """Insert or replace a prefixed extraction block in caption text."""
        text = (extracted_text or "").strip()
        if not text:
            return existing_caption or ""
        block = f"[{label}] {text}"
        existing = (existing_caption or "").strip()
        if not existing:
            return block
        lines = existing.splitlines()
        out = []
        i = 0
        consumed = False
        prefix = f"[{label}]"
        while i < len(lines):
            line = lines[i]
            if line.strip().startswith(prefix):
                # Drop existing same-label block (single-line style)
                consumed = True
                i += 1
                continue
            out.append(line)
            i += 1
        if out and out[0].strip():
            return block + "\n\n" + "\n".join(out).strip()
        return block if not out else block + "\n" + "\n".join(out).strip()

    """Attach WhatsApp-centric routes to the provided router."""
    async def _get_media_auth(base_url: str) -> tuple[dict, dict]:
        """Return (headers, params) for relay media fetch auth.

        Priority:
        1) internal/HMAC headers when configured
        2) device-auth ws_token as query param (no shared secret required)
        """
        headers = relay_auth_headers("")
        if headers:
            return headers, {}
        try:
            import httpx

            relay_payload = {"app_user_id": "local-ui", "subscribe_phones": []}
            ident = load_or_create_device_identity()
            priv_raw = base64.b64decode(str(ident.get("private_key") or "").encode())
            priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
            pub_raw = priv.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            async with httpx.AsyncClient(timeout=8.0) as client:
                ch = await client.post(
                    f"{base_url}/device/challenge",
                    json={"device_id": ident.get("device_id"), "public_key": base64.b64encode(pub_raw).decode()},
                )
                ch_obj = ch.json()
                if ch.status_code != 200 or not ch_obj.get("success"):
                    return {}, {}
                msg = str(ch_obj.get("challenge_message") or "")
                sig = priv.sign(msg.encode())
                resp = await client.post(
                    f"{base_url}/device/ws-auth",
                    json={
                        "device_id": ident.get("device_id"),
                        "challenge_id": ch_obj.get("challenge_id"),
                        "signature": base64.b64encode(sig).decode(),
                        "subscribe_phones": relay_payload.get("subscribe_phones") or [],
                    },
                )
                obj = resp.json()
                ws_token = str(obj.get("ws_token") or "").strip()
                if resp.status_code == 200 and obj.get("success") and ws_token:
                    return {}, {"ws_token": ws_token}
        except Exception:
            return {}, {}
        return {}, {}

    @router.get("/kanban/whatsapp/relay/messages")
    async def get_relay_whatsapp_messages(jid_phone: str = "", limit: int = 500, offset: int = 0, unprocessed_only: bool = False):
        """Proxy: fetch messages from the relay server (avoids CORS in browser)."""
        try:
            import httpx

            base_url = "https://www.decisionsai.net/api/whatsapp"
            if os.environ.get("DEBUG", "").upper() == "TRUE":
                base_url = "http://localhost:8090/api/whatsapp"
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = relay_auth_headers("")
                resp = await client.get(
                    f"{base_url}/messages",
                    params={
                        "jid_phone": jid_phone,
                        "limit": limit,
                        "offset": offset,
                        "unprocessed_only": str(unprocessed_only).lower(),
                    },
                    headers=headers,
                )
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception as e:
            logger.error(f"WhatsApp relay proxy error: {e}")
            return JSONResponse({"messages": [], "total": 0, "error": str(e)}, status_code=500)

    @router.post("/kanban/whatsapp/relay/mark-processed/{message_id}")
    async def mark_relay_message_processed(message_id: int):
        """Proxy: mark a message processed on the relay server."""
        try:
            import httpx

            base_url = "https://www.decisionsai.net/api/whatsapp"
            if os.environ.get("DEBUG", "").upper() == "TRUE":
                base_url = "http://localhost:8090/api/whatsapp"
            async with httpx.AsyncClient(timeout=5.0) as client:
                headers = relay_auth_headers("")
                resp = await client.post(f"{base_url}/messages/{message_id}/processed", headers=headers)
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception as e:
            logger.error(f"WhatsApp relay mark-processed proxy error: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/kanban/whatsapp/relay/clear-messages")
    async def clear_relay_whatsapp_messages():
        """Proxy: request the relay server to wipe stored WhatsApp messages."""
        try:
            import httpx

            base_url = "https://www.decisionsai.net/api/whatsapp"
            if os.environ.get("DEBUG", "").upper() == "TRUE":
                base_url = "http://localhost:8090/api/whatsapp"

            attempted = []
            async with httpx.AsyncClient(timeout=12.0) as client:
                candidates = [
                    ("post", f"{base_url}/messages/clear", {}),
                    ("delete", f"{base_url}/messages", {}),
                    ("post", f"{base_url}/messages/clear-all", {}),
                ]
                for method, url, payload in candidates:
                    payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True) if payload else ""
                    headers = relay_auth_headers(payload_str)
                    params = {}
                    if not headers:
                        _h, _p = await _get_media_auth(base_url)
                        if _h:
                            headers = _h
                        params = _p or {}
                    try:
                        if method == "post":
                            resp = await client.post(url, json=payload, headers=headers, params=params)
                        else:
                            resp = await client.delete(url, headers=headers, params=params)
                        entry = {"method": method.upper(), "url": url, "status": resp.status_code}
                        attempted.append(entry)
                        if 200 <= resp.status_code < 300:
                            body = {}
                            try:
                                body = resp.json()
                            except Exception:
                                body = {"success": True}
                            if "success" not in body:
                                body["success"] = True
                            return JSONResponse(body, status_code=resp.status_code)
                    except Exception as ex:
                        attempted.append({"method": method.upper(), "url": url, "error": str(ex)})
                        continue

            return JSONResponse(
                {
                    "success": False,
                    "error": (
                        "Could not reach the WhatsApp relay clear API (network, auth, or endpoint missing). "
                        "Try again when online, or clear local messages only via chat delete."
                    ),
                    "attempted": attempted,
                },
                status_code=502,
            )
        except Exception as e:
            logger.error(f"WhatsApp relay clear proxy error: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/kanban/whatsapp/send")
    async def send_whatsapp_message(payload: dict):
        """Proxy: send a WhatsApp message through the relay server."""
        try:
            import httpx

            base_url = "https://www.decisionsai.net/api/whatsapp"
            if os.environ.get("DEBUG", "").upper() == "TRUE":
                base_url = "http://localhost:8090/api/whatsapp"
            audio = payload.get("audio") if isinstance(payload.get("audio"), dict) else None
            media = payload.get("media") if isinstance(payload.get("media"), dict) else None
            relay_payload = {
                "jid": payload.get("jid", ""),
                "text": payload.get("text", ""),
                "caption": payload.get("caption", ""),
                "audio": {
                    "data_b64": audio.get("data_b64", ""),
                    "mime_type": audio.get("mime_type", "audio/ogg"),
                    "ptt": bool(audio.get("ptt", True)),
                    "filename": audio.get("filename", ""),
                } if audio else None,
                "media": {
                    "data_b64": media.get("data_b64", ""),
                    "mime_type": media.get("mime_type", "application/octet-stream"),
                    "filename": media.get("filename", ""),
                    "kind": media.get("kind", ""),
                } if media else None,
            }
            payload_str = json.dumps(relay_payload, separators=(",", ":"), sort_keys=True)
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = relay_auth_headers(payload_str)
                params = {}
                if not headers:
                    _h, _p = await _get_media_auth(base_url)
                    params = _p or {}
                resp = await client.post(f"{base_url}/send", json=relay_payload, headers=headers, params=params)
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception as e:
            logger.error(f"WhatsApp send proxy error: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/kanban/whatsapp/ws-auth")
    async def whatsapp_ws_auth_bundle(payload: dict):
        """Proxy: request scoped websocket auth bundle from relay."""
        try:
            import httpx

            base_url = "https://www.decisionsai.net/api/whatsapp"
            if os.environ.get("DEBUG", "").upper() == "TRUE":
                base_url = "http://localhost:8090/api/whatsapp"
            relay_payload = {"app_user_id": payload.get("app_user_id", "local-ui"), "subscribe_phones": payload.get("subscribe_phones") or []}
            payload_str = json.dumps(relay_payload, separators=(",", ":"), sort_keys=True)
            headers = relay_auth_headers(payload_str)
            async with httpx.AsyncClient(timeout=10.0) as client:
                if headers:
                    resp = await client.post(f"{base_url}/ws-auth", json=relay_payload, headers=headers)
                    return JSONResponse(content=resp.json(), status_code=resp.status_code)

                ident = load_or_create_device_identity()
                priv_raw = base64.b64decode(str(ident.get("private_key") or "").encode())
                priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
                pub_raw = priv.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
                ch = await client.post(
                    f"{base_url}/device/challenge",
                    json={"device_id": ident.get("device_id"), "public_key": base64.b64encode(pub_raw).decode()},
                )
                ch_obj = ch.json()
                if ch.status_code != 200 or not ch_obj.get("success"):
                    return JSONResponse(content=ch_obj, status_code=ch.status_code)
                msg = str(ch_obj.get("challenge_message") or "")
                sig = priv.sign(msg.encode())
                resp = await client.post(
                    f"{base_url}/device/ws-auth",
                    json={
                        "device_id": ident.get("device_id"),
                        "challenge_id": ch_obj.get("challenge_id"),
                        "signature": base64.b64encode(sig).decode(),
                        "subscribe_phones": relay_payload.get("subscribe_phones") or [],
                    },
                )
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception as e:
            logger.error(f"WhatsApp ws-auth proxy error: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/kanban/whatsapp/messages/{message_id}/processed")
    async def mark_whatsapp_message_processed(message_id: int):
        """Mark a WhatsApp message as processed."""
        with get_session() as s:
            msg = s.query(WhatsAppMessage).get(message_id)
            if not msg:
                raise HTTPException(404, "Message not found")
            msg.processed = True
            msg.processed_date = datetime.utcnow()
            return JSONResponse({"success": True})

    @router.delete("/kanban/whatsapp/messages/{message_id}")
    async def delete_whatsapp_message(message_id: int):
        """Delete a single WhatsApp message."""
        with get_session() as s:
            msg = s.query(WhatsAppMessage).get(message_id)
            if not msg:
                raise HTTPException(404, "Message not found")
            if msg.media_local_path and os.path.exists(msg.media_local_path):
                try:
                    os.remove(msg.media_local_path)
                except Exception:
                    pass
            s.delete(msg)
            return JSONResponse({"success": True})

    @router.delete("/kanban/whatsapp/chat/{jid_phone}")
    async def delete_whatsapp_chat(jid_phone: str):
        """Delete all messages from a WhatsApp chat (by phone number)."""
        with get_session() as s:
            msgs = s.query(WhatsAppMessage).filter(WhatsAppMessage.jid_phone == jid_phone).all()
            for msg in msgs:
                if msg.media_local_path and os.path.exists(msg.media_local_path):
                    try:
                        os.remove(msg.media_local_path)
                    except Exception:
                        pass
            count = len(msgs)
            for msg in msgs:
                s.delete(msg)
            return JSONResponse({"success": True, "deleted": count})

    @router.delete("/kanban/whatsapp/chats")
    async def delete_all_whatsapp_chats():
        """Delete all stored WhatsApp messages across all chats."""
        with get_session() as s:
            msgs = s.query(WhatsAppMessage).all()
            chat_ids = {m.jid_phone for m in msgs if m.jid_phone}
            for msg in msgs:
                if msg.media_local_path and os.path.exists(msg.media_local_path):
                    try:
                        os.remove(msg.media_local_path)
                    except Exception:
                        pass
            deleted = len(msgs)
            for msg in msgs:
                s.delete(msg)
            return JSONResponse({"success": True, "deleted": deleted, "deleted_chats": len(chat_ids)})

    @router.post("/kanban/whatsapp/messages/mark-snapshot-group")
    async def mark_whatsapp_messages_snapshot_group(payload: dict):
        """Mark WhatsApp messages with a snapshot group ID."""
        jid_phone = payload.get("jid_phone", "")
        snapshot_group = payload.get("snapshot_group", "")
        message_ids = payload.get("message_ids", [])
        if not snapshot_group:
            raise HTTPException(400, "snapshot_group required")
        with get_session() as s:
            if message_ids:
                msgs = s.query(WhatsAppMessage).filter(WhatsAppMessage.id.in_(message_ids)).all()
            else:
                if not jid_phone:
                    raise HTTPException(400, "jid_phone or message_ids required")
                msgs = s.query(WhatsAppMessage).filter(WhatsAppMessage.jid_phone == jid_phone).all()
            for msg in msgs:
                msg.snapshot_group = snapshot_group
            s.commit()
            return JSONResponse({"success": True, "count": len(msgs)})

    @router.get("/kanban/whatsapp/media")
    async def get_whatsapp_media(path: str = ""):
        """Serve a WhatsApp media file for display in the UI."""
        if not path:
            raise HTTPException(400, "path parameter required")
        media_dir = os.path.realpath(os.path.join(DB_DIR, "whatsapp_media"))
        basename = os.path.basename(path)
        full_path = os.path.realpath(os.path.join(media_dir, basename))
        if not full_path.startswith(media_dir):
            raise HTTPException(403, "Access denied")
        if not os.path.exists(full_path):
            raise HTTPException(404, "File not found")
        ext_media = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".ogg": "audio/ogg",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".opus": "audio/opus",
            ".mp4": "video/mp4",
            ".3gp": "video/3gpp",
            ".pdf": "application/pdf",
        }
        ext = os.path.splitext(full_path)[1].lower()
        media_type = ext_media.get(ext, "application/octet-stream")
        return FileResponse(full_path, media_type=media_type)

    @router.get("/kanban/whatsapp/chats")
    async def get_whatsapp_chats(limit: int = 100, offset: int = 0, search: str = ""):
        """Get the WhatsApp chat list from the Baileys service."""
        try:
            from PyQt6.QtWidgets import QApplication

            _app = QApplication.instance()
            whatsapp_manager = getattr(_app, "whatsapp_manager", None) if _app else None
            if not whatsapp_manager:
                return JSONResponse({"chats": [], "total": 0, "error": "WhatsApp not connected"})
            result = whatsapp_manager.get_chats(limit=limit, offset=offset, search=search)
            return JSONResponse(result)
        except Exception as e:
            logger.error(f"WhatsApp chats query error: {e}")
            return JSONResponse({"chats": [], "total": 0, "error": str(e)})

    @router.post("/kanban/whatsapp/messages/{message_id}/analyze-media")
    async def analyze_whatsapp_message_media(message_id: int):
        """On-demand media extraction for WhatsApp messages.

        - Voice notes / audio / video => transcription
        - Images => OCR text extraction
        Result is written into message caption with [Transcription] / [OCR] prefix.
        """
        with get_session() as s:
            msg = s.query(WhatsAppMessage).get(message_id)
            if not msg:
                raise HTTPException(404, "Message not found")
            local_path = (msg.media_local_path or "").strip()
            if not local_path:
                raise HTTPException(409, "Media not cached yet. Open/download the media first.")

            if not os.path.exists(local_path):
                raise HTTPException(404, "Media file not found on disk")

            media_type = msg.media_type or ""
            media_mime_type = msg.media_mime_type or ""
            extracted = ""
            label = ""

            try:
                if _is_voice_type(media_type, media_mime_type):
                    from distr.core.audio.voice_cloning import transcribe_audio_file

                    extracted = (transcribe_audio_file(local_path) or "").strip()
                    label = "Transcription"
                elif _is_video_type(media_type, media_mime_type):
                    from distr.core.audio.voice_cloning import transcribe_audio_file

                    ffmpeg_path = shutil.which("ffmpeg")
                    if ffmpeg_path:
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                            wav_path = tmp_wav.name
                        try:
                            subprocess.run(
                                [ffmpeg_path, "-y", "-i", local_path, "-ar", "16000", "-ac", "1", wav_path],
                                capture_output=True,
                                timeout=180,
                                check=True,
                            )
                            extracted = (transcribe_audio_file(wav_path) or "").strip()
                        finally:
                            try:
                                os.unlink(wav_path)
                            except Exception:
                                pass
                    else:
                        # Fallback: some backends can transcribe video directly.
                        extracted = (transcribe_audio_file(local_path) or "").strip()
                    label = "Transcription"
                elif _is_image_type(media_type, media_mime_type):
                    from distr.core.agent.services.vision.locate import build_ocr_context

                    ocr_text = (build_ocr_context(local_path, max_lines=120) or "").strip()
                    if ocr_text.startswith("OCR text detected on screen:"):
                        ocr_text = ocr_text.split(":", 1)[1].strip()
                    extracted = ocr_text
                    label = "OCR"
                else:
                    raise HTTPException(400, "Unsupported media type for analysis")
            except HTTPException:
                raise
            except Exception as exc:
                logger.error("WhatsApp media analysis failed for message %s: %s", message_id, exc, exc_info=True)
                raise HTTPException(500, f"Analysis failed: {exc}") from exc

            if not extracted:
                raise HTTPException(422, "No text extracted from media")

            msg.caption = _upsert_extracted_block(msg.caption or "", label, extracted)
            s.commit()
            return JSONResponse(
                {
                    "success": True,
                    "message_id": message_id,
                    "analysis_type": label.lower(),
                    "text": extracted,
                }
            )

    @router.get("/kanban/whatsapp/relay-media/{message_id}")
    async def relay_whatsapp_media(
        message_id: int,
        format: str = "",
        wa_key: str = Query("", description="Optional WhatsApp message key if the DB column is empty/stale"),
    ):
        """Proxy media from relay server and cache locally.

        Query params:
          format: "m4a" (Safari/iOS audio), "mp3" (voice download via ffmpeg), or empty
          wa_key: optional WhatsApp message_id string (same as Baileys key id) for relay fetch
        """
        import requests as req_lib

        relay_base = "https://www.decisionsai.net/api/whatsapp"
        if os.environ.get("DEBUG", "").upper() == "TRUE":
            relay_base = "http://localhost:8090/api/whatsapp"

        def _resolve_local_media_path(raw_path: str) -> str:
            media_dir = os.path.realpath(os.path.join(DB_DIR, "whatsapp_media"))
            basename = os.path.basename(raw_path or "")
            if not basename:
                return ""
            return os.path.realpath(os.path.join(media_dir, basename))

        def _hints_from_raw(msg_row: WhatsAppMessage) -> tuple[str, str]:
            """Return (whatsapp_message_id, relay_media_path) from row + raw_data JSON."""
            wa = (msg_row.message_id or "").strip()
            path_hint = ""
            raw = (msg_row.raw_data or "").strip()
            if not raw:
                return wa, path_hint
            try:
                o = json.loads(raw)
            except Exception:
                return wa, path_hint
            if not wa:
                wa = str(o.get("message_id") or "").strip()
            if not wa:
                key = o.get("key")
                if isinstance(key, dict):
                    wa = str(key.get("id") or "").strip()
            for k in ("media_local_path",):
                v = o.get(k)
                if isinstance(v, str) and v.strip():
                    path_hint = v.strip()
                    break
            if not path_hint:
                media = o.get("media")
                if isinstance(media, dict):
                    p = media.get("local_path") or media.get("path")
                    if isinstance(p, str) and p.strip():
                        path_hint = p.strip()
            return wa, path_hint

        with get_session() as s:
            msg = s.query(WhatsAppMessage).get(message_id)
            if not msg:
                raise HTTPException(404, "WhatsApp message not found")
            msg_media_local_path = msg.media_local_path
            msg_media_mime_type = msg.media_mime_type
            msg_message_id = (msg.message_id or "").strip()
            msg_media_type = msg.media_type
            raw_wa_id, relay_path_hint = _hints_from_raw(msg)
            effective_wa_id = (wa_key or "").strip() or msg_message_id or raw_wa_id

        # ── M4A format request (Safari/iOS) ──────────────────────────────
        if format == "m4a" and msg_media_local_path:
            # Look for M4A transcode alongside the OGG file
            ogg_path = _resolve_local_media_path(msg_media_local_path)
            m4a_path = os.path.splitext(ogg_path)[0] + ".m4a"
            if os.path.exists(m4a_path):
                return FileResponse(m4a_path, media_type="audio/mp4")
            # Try relay server for M4A
            if effective_wa_id:
                try:
                    media_headers, media_params = await _get_media_auth(relay_base)
                    media_resp = req_lib.get(
                        f"{relay_base}/media/{quote(str(effective_wa_id), safe='')}/m4a",
                        headers=media_headers,
                        params=media_params,
                        timeout=10,
                    )
                    if media_resp.status_code == 200:
                        return Response(content=media_resp.content, media_type="audio/mp4")
                except Exception:
                    pass
            raise HTTPException(404, "M4A transcode not available")

        # ── MP3 format (voice download) — transcode cached local file with ffmpeg ─
        if format == "mp3":
            import shutil
            import subprocess

            if not msg_media_local_path:
                raise HTTPException(404, "Media not available yet — open the message once to cache audio")
            local_src = _resolve_local_media_path(msg_media_local_path)
            if not local_src or not os.path.exists(local_src):
                raise HTTPException(404, "Media file not found")
            mp3_path = os.path.splitext(local_src)[0] + ".wa.mp3"
            if os.path.exists(mp3_path):
                return FileResponse(mp3_path, media_type="audio/mpeg")
            ffmpeg_path = shutil.which("ffmpeg")
            if not ffmpeg_path:
                raise HTTPException(503, "MP3 export requires ffmpeg in PATH")
            try:
                subprocess.run(
                    [ffmpeg_path, "-y", "-i", local_src, "-c:a", "libmp3lame", "-q:a", "4", mp3_path],
                    capture_output=True,
                    timeout=120,
                    check=True,
                )
            except Exception as ex:
                logger.warning("relay-media MP3 transcode failed: %s", ex)
                raise HTTPException(503, "MP3 conversion failed") from ex
            if os.path.exists(mp3_path):
                return FileResponse(mp3_path, media_type="audio/mpeg")
            raise HTTPException(503, "MP3 conversion failed")

        if msg_media_local_path:
            media_dir = os.path.realpath(os.path.join(DB_DIR, "whatsapp_media"))
            full_path = _resolve_local_media_path(msg_media_local_path)
            if not full_path:
                raise HTTPException(404, "Media path is empty")
            if not full_path.startswith(media_dir):
                raise HTTPException(403, "Access denied")
            if os.path.exists(full_path):
                mime = msg_media_mime_type or "application/octet-stream"
                return FileResponse(full_path, media_type=mime)

        # ── Fallback: try relay server ────────────────────────────────────
        # Never use local numeric PK as relay key — relay indexes by WhatsApp message_id string.
        async def _fetch_relay_media() -> tuple[bytes, str, str]:
            media_headers, media_params = await _get_media_auth(relay_base)
            if not media_headers and not media_params:
                logger.warning("relay-media: no relay auth (internal token or ws_token); inbound fetch may 401")

            candidates: list[tuple[str, str]] = []
            if effective_wa_id:
                candidates.append((f"{relay_base}/media/{quote(str(effective_wa_id), safe='')}", "by_wa_id"))
            if relay_path_hint:
                candidates.append((f"{relay_base}/media", "by_path"))
            seen_urls: set[str] = set()
            for url, mode in candidates:
                if mode == "by_path":
                    params = dict(media_params or {})
                    params["path"] = relay_path_hint.lstrip("/")
                    key = f"path:{params.get('path')}"
                else:
                    params = media_params or {}
                    key = url
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                try:
                    media_resp = req_lib.get(url, headers=media_headers, params=params, timeout=15)
                    if media_resp.status_code == 200:
                        ct = media_resp.headers.get("Content-Type", msg_media_mime_type or "application/octet-stream")
                        return media_resp.content, ct, effective_wa_id or ""
                    logger.warning(
                        "relay-media: relay GET %s returned %s (wa_id=%r path_hint=%r)",
                        url,
                        media_resp.status_code,
                        effective_wa_id,
                        relay_path_hint[:80] if relay_path_hint else "",
                    )
                except Exception as ex:
                    logger.warning("relay-media: relay GET %s failed: %s", url, ex)

            raise HTTPException(404, "Media not available — reconnect WhatsApp to download")

        try:
            media_bytes, content_type, ack_wa_id = await _fetch_relay_media()
        except HTTPException:
            raise

        try:
            media_dir_path = os.path.join(DB_DIR, "whatsapp_media")
            os.makedirs(media_dir_path, exist_ok=True)
            ext_map = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "audio/ogg": ".ogg",
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
                "audio/webm": ".webm",
                "audio/opus": ".opus",
                "video/mp4": ".mp4",
                "video/webm": ".webm",
                "application/pdf": ".pdf",
            }
            ext = ext_map.get(content_type.split(";")[0], ".bin")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"whatsapp_{msg_media_type or 'media'}_{ts}{ext}"
            dest = os.path.join(media_dir_path, filename)
            with open(dest, "wb") as f:
                f.write(media_bytes)
            media_headers, media_params = await _get_media_auth(relay_base)
            with get_session() as upd_s:
                db_msg = upd_s.query(WhatsAppMessage).get(message_id)
                if db_msg:
                    db_msg.media_local_path = dest
                    db_msg.media_file_length = len(media_bytes)
                    if not db_msg.media_filename:
                        db_msg.media_filename = filename
                    if not (db_msg.message_id or "").strip() and ack_wa_id:
                        db_msg.message_id = ack_wa_id
                    if db_msg.media_type in ("voice", "audio", "ptt"):
                        try:
                            from distr.core.audio.voice_cloning import transcribe_audio_file

                            transcription = transcribe_audio_file(dest)
                            if transcription:
                                prefix = "[Transcription] "
                                existing_caption = db_msg.caption or ""
                                if prefix not in existing_caption:
                                    db_msg.caption = f"{prefix}{transcription}\n\n{existing_caption}".strip()
                        except Exception as tr_err:
                            logger.warning(f"Voice transcription failed for relay media {message_id}: {tr_err}")
                    upd_s.commit()
            logger.info(f"Cached relay media for msg {message_id}")
            ack_ok = False
            if ack_wa_id:
                for _ in range(3):
                    try:
                        ack_resp = req_lib.post(
                            f"{relay_base}/messages/by-wa-id/{ack_wa_id}/processed",
                            headers=media_headers,
                            params={
                                **(media_params or {}),
                                "purge_media": "false",
                                "client_media_local_path": dest,
                            },
                            timeout=10,
                        )
                        if 200 <= int(ack_resp.status_code) < 300:
                            ack_ok = True
                            break
                    except Exception:
                        pass
                if not ack_ok:
                    logger.warning("Relay processed/purge ack failed for %s after retries", ack_wa_id)
        except Exception as e:
            logger.warning(f"Could not cache relay media: {e}")
        return Response(content=media_bytes, media_type=content_type)

    @router.post("/kanban/tickets/from-whatsapp/{message_id}")
    async def create_ticket_from_whatsapp(message_id: int, payload: dict):
        """Create a Ticket Board ticket from a WhatsApp message."""
        with get_session() as s:
            msg = s.query(WhatsAppMessage).get(message_id)
            if not msg:
                raise HTTPException(404, "WhatsApp message not found")
            board_id = payload.get("board_id")
            if not board_id:
                raise HTTPException(400, "board_id is required")
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            source_lane_name = board.agent_source_lane or ""
            lane = None
            if source_lane_name:
                lane = s.query(KanbanLane).filter_by(board_id=board_id, name=source_lane_name).first()
            if not lane:
                lane = s.query(KanbanLane).filter_by(board_id=board_id).order_by(KanbanLane.position).first()
            if not lane:
                raise HTTPException(400, "Board has no lanes")
            sender = msg.sender_push_name or msg.sender_phone or msg.jid_phone or "Unknown"
            title = f"[WA] {sender}: {msg.text[:80]}" if msg.text else f"[WA] {sender}: {msg.media_type or 'message'}"
            if msg.caption:
                title = f"[WA] {sender}: {msg.caption[:80]}"
            desc_parts = [f"WhatsApp message from {sender}"]
            if msg.sender_phone:
                desc_parts.append(f"Phone: {msg.sender_phone}")
            if msg.text:
                desc_parts.append(f"\n{msg.text}")
            if msg.caption:
                desc_parts.append(f"Caption: {msg.caption}")
            if msg.media_type:
                desc_parts.append(f"Media: {msg.media_type}")
                if msg.media_filename:
                    desc_parts.append(f"File: {msg.media_filename}")
                if msg.media_local_path:
                    desc_parts.append(f"Path: {msg.media_local_path}")
            description = "\n".join(desc_parts)
            existing = s.query(KanbanTicket).filter_by(whatsapp_message_id=message_id).first()
            if existing:
                return JSONResponse({"success": True, "id": existing.id, "message": "Ticket already exists"})
            max_pos = max([t.position for t in lane.tickets], default=-1)
            ticket = KanbanTicket(
                lane_id=lane.id,
                title=title,
                description=description,
                priority="medium",
                position=max_pos + 1,
                whatsapp_message_id=message_id,
                whatsapp_message_wa_id=msg.message_id,
            )
            s.add(ticket)
            msg.processed = True
            msg.processed_date = datetime.utcnow()
            if msg.media_local_path and os.path.exists(msg.media_local_path):
                safe_name = os.path.basename(msg.media_local_path)
                ticket_file = KanbanTicketFile(
                    ticket_id=ticket.id if ticket.id else 0,
                    filename=safe_name,
                    file_path=msg.media_local_path,
                    description=f"WhatsApp {msg.media_type}: {safe_name}" if msg.media_type else safe_name,
                )
                s.flush()
                ticket_file.ticket_id = ticket.id
                s.add(ticket_file)
            s.flush()
            return JSONResponse({"success": True, "id": ticket.id})

    async def _broadcast_wa_ws(event_data: str):
        dead = set()
        for ws in list(_wa_ws_clients):
            try:
                await ws.send_text(event_data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            _wa_ws_clients.discard(ws)

    def _hook_whatsapp_signal():
        global _wa_sse_hooked, _wa_sse_loop
        if _wa_sse_hooked:
            return
        try:
            from PyQt6.QtWidgets import QApplication

            _app = QApplication.instance()
            whatsapp_manager = getattr(_app, "whatsapp_manager", None) if _app else None
            if not whatsapp_manager:
                return
            try:
                _wa_sse_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

            def _on_message_received(data: dict):
                event_data = json.dumps(
                    {
                        "type": "whatsapp_message",
                        "jid_phone": data.get("jid_phone", ""),
                        "sender_phone": data.get("sender", {}).get("phone", ""),
                        "sender_push_name": data.get("sender", {}).get("push_name", ""),
                        "text": data.get("text", ""),
                        "media_type": data.get("media", {}).get("type") if isinstance(data.get("media"), dict) else None,
                    }
                )
                for q in list(_wa_sse_queues):
                    try:
                        if _wa_sse_loop and not _wa_sse_loop.is_closed():
                            _wa_sse_loop.call_soon_threadsafe(q.put_nowait, event_data)
                        else:
                            q.put_nowait(event_data)
                    except Exception:
                        if q in _wa_sse_queues:
                            _wa_sse_queues.remove(q)
                if _wa_sse_loop and not _wa_sse_loop.is_closed():
                    _wa_sse_loop.call_soon_threadsafe(lambda: asyncio.create_task(_broadcast_wa_ws(event_data)))

            whatsapp_manager.message_received.connect(_on_message_received)
            _wa_sse_hooked = True
            logger.info("WhatsApp SSE signal hook connected")
        except Exception as e:
            logger.debug(f"WhatsApp SSE hook failed: {e}")

    @router.get("/kanban/whatsapp/stream")
    async def stream_whatsapp_messages():
        """SSE endpoint for real-time WhatsApp messages."""
        _hook_whatsapp_signal()

        async def event_generator():
            q = asyncio.Queue()
            _wa_sse_queues.append(q)
            try:
                yield ": keepalive\n\n"
                while True:
                    try:
                        data = await asyncio.wait_for(q.get(), timeout=30.0)
                        yield f"event: whatsapp_message\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                if q in _wa_sse_queues:
                    _wa_sse_queues.remove(q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.websocket("/kanban/ws/whatsapp")
    async def whatsapp_websocket(websocket: WebSocket):
        """WebSocket stream for real-time WhatsApp thread updates."""
        origin = websocket.headers.get("origin")
        if origin and not is_allowed_local_origin(origin):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        _hook_whatsapp_signal()
        await websocket.accept()
        _wa_ws_clients.add(websocket)
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    await websocket.send_text('{"type":"ping"}')
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            _wa_ws_clients.discard(websocket)
