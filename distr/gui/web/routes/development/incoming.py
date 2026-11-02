"""Incoming HTTP boundary. Existing URLs remain compatible."""
from fastapi import Request
from fastapi.responses import JSONResponse, Response
import asyncio
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


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
            from distr.core.incoming.proposals import fetch_attachment

            data, mime = await asyncio.to_thread(fetch_attachment, path)
            return Response(content=data, media_type=mime, headers={"Cache-Control": "private, max-age=600"})
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
            data = path.read_bytes()
            mime = str(record.get("mime_type") or "application/octet-stream")
            headers = {
                "Cache-Control": "private, max-age=600",
                "Content-Disposition": f'inline; filename="{Path(str(record.get("name") or path.name)).name}"',
            }
            return Response(content=data, media_type=mime, headers=headers)
        except FileNotFoundError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=404)
        except Exception as e:
            logger.warning("Managed proposal file serve failed: %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=502)


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

            chat_id = await asyncio.to_thread(create_proposal_thread, proposal, start_work=True)
            return JSONResponse({"success": True, "chat_id": chat_id})
        except Exception as e:
            logger.error("Proposal thread creation failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


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

            dismissed = await asyncio.to_thread(dismiss_proposal, proposal)
            return JSONResponse({"success": True, "dismissed": dismissed})
        except Exception as e:
            logger.error("Proposal dismiss failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


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

