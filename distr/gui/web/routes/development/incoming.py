"""Incoming HTTP boundary. Existing URLs remain compatible."""
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
import asyncio
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
PROPOSAL_READ_TIMEOUT_SECONDS = 60


def register_routes(router, templates):
    @router.put("/workflows/studio/incoming/proposals/auto-accept")
    async def set_proposal_auto_accept(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "A JSON setting is required"}, status_code=422)
        if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
            return JSONResponse({"error": "enabled must be true or false"}, status_code=422)
        from distr.core.db import Settings, get_session

        # One targeted setting write, preserving every other stored setting.
        with get_session() as db:
            settings = db.query(Settings).first()
            if settings is None:
                settings = Settings()
                db.add(settings)
            settings.incoming_proposals_auto_accept = body["enabled"]
            db.commit()
        from distr.core.incoming.proposals import invalidate_proposals_list_cache
        invalidate_proposals_list_cache()
        return JSONResponse({"auto_accept": body["enabled"]})

    @router.get("/workflows/intake/inbox")
    async def workflow_intake_inbox(limit: int = 40):
        """Mission Control inbox for channel WorkIntake decisions."""
        try:
            from distr.core.work_intake import get_work_intake_service

            items = await asyncio.to_thread(get_work_intake_service().list_inbox, limit=limit)
            return JSONResponse({"items": items})
        except Exception as e:
            logger.error("Workflow intake inbox failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/incoming")
    async def workflow_studio_incoming(limit: int = 100):
        """Unified channel messages and board links for Development."""
        try:
            from distr.core.incoming.service import list_development_incoming

            return JSONResponse(await asyncio.to_thread(list_development_incoming, limit=limit))
        except Exception as e:
            logger.error("Development incoming load failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/incoming/proposals")
    async def workflow_studio_incoming_proposals(limit: int = 100, refresh: int = 0):
        """Incoming proposals: Tensology (canonical) reconciled with local channel candidates.

        Cached in-process; pass refresh=1 (Resync) to rebuild from Tensology/local sources.
        """
        try:
            from distr.core.incoming.proposals import list_incoming_proposals

            return JSONResponse(await asyncio.to_thread(
                list_incoming_proposals, limit=limit, force=bool(refresh),
            ))
        except Exception as e:
            logger.error("Incoming proposals load failed: %s", e, exc_info=True)
            return JSONResponse({"proposals": [], "counts": {}, "error": str(e)}, status_code=500)


    @router.get("/workflows/studio/incoming/proposals/attachment")
    async def workflow_studio_proposal_attachment(path: str = ""):
        """Serve a Tensology proposal attachment from managed local cache (download once)."""
        try:
            from distr.core.incoming.media import cached_tensology_attachment_file

            cached, mime = await asyncio.to_thread(cached_tensology_attachment_file, path)
            headers = {"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"}
            if mime == 'application/octet-stream':
                headers['Content-Disposition'] = 'attachment'
            return FileResponse(cached, media_type=mime, headers=headers)
        except ValueError as e:
            logger.warning("Proposal attachment rejected: %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=404)
        except Exception as e:
            # Upstream provider/download failures must surface in the UI, not as a silent blank.
            logger.warning("Proposal attachment fetch failed: %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=502)

    @router.post("/workflows/studio/incoming/proposals/files/ensure")
    async def workflow_studio_proposal_files_ensure(request: Request):
        """Download/store proposal files once and return public URLs + any download errors."""
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"success": False, "error": "A proposal is required."}, status_code=422)
        proposal = (body or {}).get("proposal") if isinstance(body, dict) else None
        if not isinstance(proposal, dict):
            return JSONResponse({"success": False, "error": "A proposal is required."}, status_code=422)
        try:
            from distr.core.incoming.media import ensure_proposal_attachments, public_managed_attachments

            def _run():
                project_id = proposal.get("project_id")
                result = ensure_proposal_attachments(
                    proposal,
                    int(project_id) if project_id else None,
                    strict=False,
                )
                proposal_id = str(proposal.get("id") or "")
                public = public_managed_attachments(result["attachments"], proposal_id=proposal_id)
                failures = public_managed_attachments(result["failures"], proposal_id=proposal_id)
                return {"attachments": public, "failures": failures}

            payload = await asyncio.to_thread(_run)
            return JSONResponse({"success": True, **payload})
        except Exception as e:
            logger.warning("Proposal files ensure failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/workflows/studio/incoming/proposals/files/{attachment_id}")
    async def workflow_studio_proposal_managed_file(attachment_id: str, proposal_id: str = ""):
        """Serve a previously ensured managed proposal attachment by id."""
        # Prefer proposal from list cache; fall back to query body is not available on GET.
        try:
            from distr.core.incoming.media import resolve_proposal_managed_attachment
            from distr.core.incoming.proposals import list_incoming_proposals

            def _run():
                listing = list_incoming_proposals(refresh_local=False)
                proposal = next((row for row in listing.get("proposals") or [] if str(row.get("id")) == str(proposal_id)), None)
                if proposal is None and str(proposal_id).startswith("tensology:"):
                    # Dismissed/sent proposals are absent from Incoming list but may still have
                    # managed files or a Tensology work-request detail we can ensure from.
                    from distr.core.incoming.proposals import _from_tensology, _project_map
                    from distr.core.tensology_client import configured_tensology_client
                    wid = str(proposal_id).split(":", 1)[1]
                    client = configured_tensology_client(source="decisionsai-incoming-attachment")
                    detail = client.get(f"work-requests/{wid}")
                    proposal = _from_tensology(detail, _project_map())
                if proposal is None:
                    raise FileNotFoundError("The proposal is no longer available. Refresh Incoming.")
                path, record = resolve_proposal_managed_attachment(
                    proposal, attachment_id, int(proposal["project_id"]) if proposal.get("project_id") else None,
                )
                return path, record

            path, record = await asyncio.to_thread(_run)
            mime = str(record.get("mime_type") or "application/octet-stream")
            # Browsers accept audio/ogg; drop long parameter lists that break <audio>.
            media_type = mime.split(";", 1)[0].strip() or "application/octet-stream"
            if media_type == "audio/ogg" and "codecs=" in mime.lower():
                media_type = "audio/ogg"
            filename = Path(str(record.get("name") or path.name)).name.replace('"', "")
            headers = {
                "Cache-Control": "private, max-age=600",
                "Content-Disposition": f'inline; filename="{filename}"',
            }
            return FileResponse(path, media_type=media_type, headers=headers)
        except FileNotFoundError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=404)
        except Exception as e:
            logger.warning("Managed proposal file serve failed: %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=502)



    @router.get("/workflows/studio/incoming/mail/attachment")
    async def workflow_studio_incoming_mail_attachment(
        message_id: str = "",
        attachment_id: str = "",
        content_id: str = "",
        provider: str = "",
        filename: str = "",
    ):
        """Serve a Gmail or Mailshot Incoming attachment/inline image for the mail reader.

        Session-auth only — no internal_token. Used for cid: rewrite and the
        fixed attachment bar previews. Mailshot uses the local tunnel; Gmail
        uses the connected Google Workspace account.
        """
        message_id = str(message_id or "").strip()
        attachment_id = str(attachment_id or "").strip()
        content_id = str(content_id or "").strip().strip("<>")
        provider_key = str(provider or "").strip().lower()
        filename_hint = str(filename or "").strip()
        if not provider_key and message_id.startswith("email-"):
            provider_key = "mailshot"
        if not message_id or (not attachment_id and not content_id):
            return JSONResponse({"success": False, "error": "message_id and attachment_id or content_id are required."}, status_code=422)
        try:
            if provider_key in {"mailshot", "tensology"}:
                if not attachment_id:
                    return JSONResponse({"success": False, "error": "attachment_id is required for Mailshot previews."}, status_code=422)
                from distr.core.incoming.mailshot_media import fetch_mailshot_attachment_bytes

                def _run_mailshot():
                    return fetch_mailshot_attachment_bytes(
                        message_id,
                        attachment_id,
                        filename_hint=filename_hint,
                    )

                data, mime, resolved_name = await asyncio.to_thread(_run_mailshot)
            else:
                from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

                def _run_gmail():
                    connector = GoogleWorkspaceConnector()
                    if not connector.is_connected():
                        raise FileNotFoundError("Connect Gmail in Settings to preview attachments.")
                    result = connector.fetch_email_attachment_bytes(
                        message_id,
                        attachment_id,
                        content_id=content_id,
                    )
                    if not result:
                        raise FileNotFoundError("Attachment could not be loaded.")
                    return result

                data, mime, resolved_name = await asyncio.to_thread(_run_gmail)
            media_type = (mime or "application/octet-stream").split(";", 1)[0].strip() or "application/octet-stream"
            safe_name = Path(resolved_name or filename_hint or "attachment").name.replace('"', "")
            return Response(
                content=data,
                media_type=media_type,
                headers={
                    "Cache-Control": "private, max-age=600",
                    "Content-Disposition": f'inline; filename="{safe_name}"',
                },
            )
        except FileNotFoundError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=404)
        except Exception as e:
            logger.warning("Incoming mail attachment serve failed: %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=502)


    @router.put("/workflows/studio/incoming/proposals/details")
    async def workflow_studio_proposal_details(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get('proposal_id'), str) or not isinstance(body.get('message_ids'), list):
                raise ValueError('A proposal and exact source messages are required.')
            from distr.core.incoming.proposals import list_incoming_proposals
            from distr.core.incoming.proposal_sync import update_proposal
            listing = await asyncio.to_thread(list_incoming_proposals, refresh_local=False)
            proposal = next((row for row in listing['proposals'] if row['id'] == body['proposal_id']), None)
            if proposal is None:
                return JSONResponse({'error': 'The proposal is no longer available. Refresh Incoming.'}, status_code=404)
            if sorted(map(str, body['message_ids'])) != sorted(map(str, proposal.get('message_ids') or [])):
                return JSONResponse({'error': 'The source snapshot changed. Reload Incoming.'}, status_code=409)
            changes = {key: value for key, value in body.items() if key not in {'proposal_id', 'message_ids', 'expected_updated_at'}}
            updated = await asyncio.to_thread(update_proposal, proposal, changes, expected_updated_at=body.get('expected_updated_at'))
            return JSONResponse({'success': True, 'proposal': updated})
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        except Exception as exc:
            from distr.core.tensology_client import TensologyApiError
            status_code = exc.status if isinstance(exc, TensologyApiError) and exc.status in {400, 403, 404, 409} else 502
            return JSONResponse({'error': str(exc)}, status_code=status_code)

    @router.put("/workflows/studio/incoming/proposals/project")
    async def workflow_studio_proposal_project(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({'error': 'A project assignment is required.'}, status_code=422)
        if (not isinstance(body, dict) or not isinstance(body.get('proposal_id'), str)
                or type(body.get('project_id')) is not int
                or not isinstance(body.get('message_ids'), list)):
            return JSONResponse({'error': 'A proposal and project are required.'}, status_code=422)
        from distr.core.incoming.proposals import list_incoming_proposals, reassign_proposal_project

        listing = await asyncio.to_thread(list_incoming_proposals, refresh_local=False)
        proposal = next((row for row in listing['proposals'] if row['id'] == body['proposal_id']), None)
        if proposal is None:
            return JSONResponse({'error': 'The proposal is no longer available. Refresh Incoming.'}, status_code=404)
        if body.get('expected_updated_at') is not None and body['expected_updated_at'] != proposal.get('updated_at'):
            return JSONResponse({'error': 'The proposal changed. Reload Incoming before saving.'}, status_code=409)
        if sorted(str(v) for v in body['message_ids']) != sorted(str(v) for v in proposal.get('message_ids', [])):
            return JSONResponse({'error': 'The proposal has changed. Refresh Incoming before changing its project.'}, status_code=409)
        try:
            updated = await asyncio.to_thread(reassign_proposal_project, proposal, body['project_id'])
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=422)
        return JSONResponse({'success': True, 'proposal': updated})


    @router.post("/workflows/studio/incoming/proposals/merge")
    async def workflow_studio_proposal_merge(request: Request):
        """Rebuild two same-project proposals into one ticket (AI merge)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        first = (body or {}).get("first") if isinstance(body, dict) else None
        second = (body or {}).get("second") if isinstance(body, dict) else None
        if not (isinstance(first, dict) and isinstance(second, dict)):
            return JSONResponse({"success": False, "error": "Two proposals are required."}, status_code=422)
        if not first.get("project_id") or str(first.get("project_id")) != str(second.get("project_id")):
            return JSONResponse(
                {"success": False, "error": "Only proposals on the same project can be merged."},
                status_code=422,
            )
        try:
            from distr.core.incoming.proposals import merge_proposals

            result = await asyncio.to_thread(merge_proposals, first, second)
            return JSONResponse({"success": True, **result})
        except Exception as e:
            logger.error("Proposal merge failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    @router.post("/workflows/studio/incoming/proposals/thread")
    async def workflow_studio_proposal_thread(request: Request):
        """Push an Incoming proposal into a Development thread."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        proposal = (body or {}).get("proposal") if isinstance(body, dict) else None
        if not isinstance(proposal, dict) or not str(proposal.get("title") or "").strip():
            return JSONResponse({"success": False, "error": "A proposal with a title is required."}, status_code=422)
        try:
            from distr.core.incoming.proposals import create_proposal_thread

            from distr.core.incoming.proposals import proposal_thread_attachment_report

            chat_id = await asyncio.to_thread(create_proposal_thread, proposal, start_work=True)
            # Files that could not be fetched never block the thread; report them.
            report = await asyncio.to_thread(proposal_thread_attachment_report, chat_id)
            return JSONResponse({"success": True, "chat_id": chat_id, **report})
        except Exception as e:
            logger.error("Proposal thread creation failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    @router.post("/workflows/studio/incoming/proposals/read")
    async def workflow_studio_proposal_read(request: Request):
        """Read an Incoming proposal aloud: LLM spoken summary + oracle direct TTS."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        text = body.get("text")
        if not isinstance(text, str):
            return JSONResponse({"success": False, "error": "Text to read must be a string."}, status_code=422)
        text = text.strip()
        if not text:
            return JSONResponse({"success": False, "error": "Text to read is required."}, status_code=422)
        proposal_id = body.get("proposal_id")
        title = str(body.get("title") or "").strip()

        from distr.core import read_response as rr
        from distr.core.settings import load_settings_from_db
        from distr.core.chat import ChatService

        if len(text) > rr.MAX_INPUT_CHARS:
            return JSONResponse({"success": False, "error": "This proposal is too long to read aloud."}, status_code=413)
        try:
            # Capture the originating Oracle chat before waiting on the model.
            current = ChatService.get_current_chat_id()
            settings = load_settings_from_db()
            spoken, provider, model = await asyncio.wait_for(
                asyncio.to_thread(rr.generate_spoken_text, text, settings),
                timeout=PROPOSAL_READ_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return JSONResponse({"success": False, "error": "Preparing speech timed out. Please try again."}, status_code=504)
        except Exception as e:
            logger.warning("Proposal read-aloud generation failed: %s", e)
            return JSONResponse({"success": False, "error": "Could not prepare the spoken version."}, status_code=502)

        if await request.is_disconnected():
            return JSONResponse({"success": False, "error": "The read request was cancelled."}, status_code=499)
        try:
            spoken_ok = bool(rr.speak_spoken_text(spoken))
        except Exception:
            logger.warning("Proposal speech dispatch failed", exc_info=True)
            spoken_ok = False
        if not spoken_ok:
            return JSONResponse({"success": False, "error": "Speech is unavailable. Check that the Oracle is running and try again."}, status_code=503)
        note_id = None
        if spoken_ok:
            try:
                from distr.core.db import get_session

                with get_session() as session:
                    note_id = rr.add_oracle_context_note_for_proposal(
                        session, spoken,
                        proposal_id=proposal_id, title=title, current_chat_id=current,
                    )
            except Exception as e:
                logger.warning("Proposal oracle context note failed: %s", e)
        return JSONResponse({
            "success": True,
            "spoken_text": spoken,
            "provider": provider,
            "model": model,
            "oracle_note_row_id": note_id,
        })


    @router.post("/workflows/studio/incoming/proposals/dismiss")
    async def workflow_studio_proposal_dismiss(request: Request):
        """Dismiss an Incoming proposal (clears it on Tensology too when it exists there)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        proposal = (body or {}).get("proposal") if isinstance(body, dict) else None
        if not isinstance(proposal, dict):
            return JSONResponse({"success": False, "error": "A proposal is required."}, status_code=422)
        try:
            from distr.core.incoming.proposals import dismiss_proposal

            from distr.core.incoming.proposals import dismissal_receipt_keys

            dismissed = await asyncio.to_thread(dismiss_proposal, proposal)
            receipt_keys = dismissal_receipt_keys(proposal)
            return JSONResponse({
                "success": True, "dismissed": dismissed,
                "key": receipt_keys[0], "receipt_keys": receipt_keys,
                "upstream_dismissed": any(k.startswith("tensology:") for k in receipt_keys),
            })
        except Exception as e:
            logger.error("Proposal dismiss failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/workflows/studio/incoming/proposals/restore")
    async def workflow_studio_proposal_restore(request: Request):
        """Undo a dismissal: delete the local receipt(s). Never calls Tensology."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        key = str((body or {}).get("key") or "").strip() if isinstance(body, dict) else ""
        if not key:
            return JSONResponse({"success": False, "error": "A dismissal key is required."}, status_code=422)
        try:
            from distr.core.incoming.proposals import undismiss_proposal

            result = await asyncio.to_thread(undismiss_proposal, key)
            return JSONResponse({"success": True, **result})
        except Exception as e:
            logger.error("Proposal restore failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.delete("/workflows/studio/incoming/proposals/dismissed")
    async def workflow_studio_hide_dismissed_proposal(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        key = str(body.get('key') or '').strip() if isinstance(body, dict) else ''
        if not key:
            return JSONResponse({'success':False, 'error':'A dismissal key is required.'}, status_code=422)
        try:
            from distr.core.incoming.proposals import hide_dismissed_proposal
            result = await asyncio.to_thread(hide_dismissed_proposal, key)
            return JSONResponse({'success':True, **result})
        except Exception:
            logger.exception('Dismissal history hide failed')
            return JSONResponse({'success':False, 'error':'Could not remove the entry from history.'}, status_code=500)

    @router.get("/workflows/studio/incoming/proposals/dismissed")
    async def workflow_studio_proposals_dismissed(limit: int = 20):
        """Recently dismissed proposals (titles only when still cached in memory)."""
        try:
            from distr.core.incoming.proposals import list_dismissed_proposals

            rows = await asyncio.to_thread(list_dismissed_proposals, limit)
            return JSONResponse({"success": True, "dismissed": rows,
                                 "restore_immediate": True, "history_delete": True})
        except Exception as e:
            logger.error("Dismissed proposals load failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "dismissed": [], "error": str(e)}, status_code=500)


    @router.post("/workflows/intake/inbox/{event_id}/action")
    async def workflow_intake_inbox_action(event_id: int, request: Request):
        """Continue / stop / steer / push / dismiss an inbox item."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = str((body or {}).get("action") or "").strip()
        message = str((body or {}).get("message") or "").strip()
        try:
            from distr.core.work_intake import get_work_intake_service

            result = await asyncio.to_thread(
                get_work_intake_service().resolve_inbox_item,
                int(event_id),
                action=action,
                message=message,
            )
            status = 200 if result.get("success") else 400
            return JSONResponse(result, status_code=status)
        except Exception as e:
            logger.error("Workflow intake inbox action failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    @router.post("/workflows/intake/ingest")
    async def workflow_intake_ingest(request: Request):
        """Web Mission Control entry into the shared WorkIntake pipeline."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            from distr.core.work_intake import WorkIntake, get_work_intake_service

            payload = dict(body or {})
            payload.setdefault("source", "web")
            decision = await asyncio.to_thread(
                get_work_intake_service().ingest,
                WorkIntake.from_payload(payload),
            )
            return JSONResponse({"success": True, "decision": decision.to_dict()})
        except Exception as e:
            logger.error("Workflow intake ingest failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    @router.post("/workflows/incoming/gmail/ingest")
    async def workflow_incoming_gmail_ingest(request: Request):
        """Promote a displayed Gmail thread into WorkIntake(source=gmail)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            from distr.core.incoming.service import ingest_gmail_as_work

            decision = await asyncio.to_thread(
                ingest_gmail_as_work,
                subject=str((body or {}).get("subject") or ""),
                body=str((body or {}).get("text") or (body or {}).get("body") or ""),
                message_id=str((body or {}).get("source_message_id") or (body or {}).get("message_id") or ""),
                thread_id=str((body or {}).get("source_thread_id") or (body or {}).get("thread_id") or ""),
                sender=str((body or {}).get("sender") or ""),
                board_hint=str((body or {}).get("board_hint") or ""),
                project_hint=str((body or {}).get("project_hint") or ""),
                force_ticket=bool((body or {}).get("force_ticket", True)),
            )
            return JSONResponse({"success": True, "decision": decision})
        except Exception as e:
            logger.error("Gmail WorkIntake ingest failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    @router.post("/workflows/studio/incoming/mail/reply")
    async def workflow_studio_incoming_mail_reply(request: Request):
        """Quick Reply from Incoming Gmail/Mailshot readers. dry_run never sends."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"success": False, "error": "A JSON body is required."}, status_code=422)
        try:
            from distr.core.incoming.mail_reply import send_incoming_mail_reply

            result = await asyncio.to_thread(
                send_incoming_mail_reply,
                channel=str(body.get("channel") or ""),
                to=str(body.get("to") or ""),
                subject=str(body.get("subject") or ""),
                body=str(body.get("body") or ""),
                message_id=str(body.get("message_id") or body.get("source_message_id") or ""),
                dry_run=bool(body.get("dry_run")),
            )
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Incoming mail reply failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

