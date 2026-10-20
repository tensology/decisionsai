from __future__ import annotations

import json
import os
import platform
import subprocess
import uuid
from pathlib import Path
from typing import Any, ClassVar, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.tensology_client import TensologyApiError, configured_tensology_client


class TensologyWorkspaceInput(BaseModel):
    action: Literal[
        "capabilities", "list_mail", "read_mail", "read_latest_mail", "list_drafts", "save_draft", "send_mail",
        "list_customers", "list_projects", "sync_project", "list_time_entries",
        "create_time_entry", "update_time_entry", "list_invoices", "get_invoice",
        "create_invoice", "update_invoice",
        "list_repositories", "repository_status", "repository_search", "repository_read_file",
        "repository_git_metadata", "repository_find_related_files", "laya_assess_discussion",
    ] = Field(description="The Tensology operation to perform.")
    params: dict[str, Any] = Field(default_factory=dict, description="Operation parameters.")
    approved: bool = Field(
        default=False,
        description=(
            "For send_mail and invoice changes only. True only after the user explicitly approved the exact action."
        ),
    )
    idempotency_key: str = Field(default="", description="Stable retry key for mutations; generated when omitted.")


class TensologyWorkspaceTool(BaseTool):
    name: str = "tensology_workspace"
    description: str = (
        "Use the central Tensology connection for Mailshot inbox and drafts, customers, projects, calendar time "
        "entries, and invoices. Prefer this over Gmail when the user says Tensology mail or Mailshot, or when the "
        "active project is linked to Tensology. Reading and drafting are safe. Never send mail unless the user "
        "explicitly instructed sending the exact content or approved a recipient, subject, and body preview; "
        "otherwise save a draft and ask. Use read_latest_mail for the complete body of the newest matching message; "
        "set params.include_thread=true when the user asks for the complete conversation thread."
    )
    args_schema: type[BaseModel] = TensologyWorkspaceInput

    _ACTION_ALIASES: ClassVar[dict[str, str]] = {
        "check_inbox": "list_mail",
        "mail_inbox": "list_mail",
        "list_inbox": "list_mail",
        "check_mail": "list_mail",
        "read_emails": "list_mail",
        "read_email": "read_mail",
        "get_email": "read_mail",
        "draft_email": "save_draft",
        "create_draft": "save_draft",
        "send_email": "send_mail",
    }

    @classmethod
    def _canonical_action(cls, action: str) -> str:
        return cls._ACTION_ALIASES.get(str(action or "").strip().lower(), str(action or "").strip())

    def normalize_tool_args(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Canonicalize common mail vocabulary before schema validation."""
        normalized = dict(arguments or {})
        if "action" in normalized:
            normalized["action"] = self._canonical_action(normalized["action"])
        return normalized

    def _run(self, action: str, params: dict | None = None, approved: bool = False, idempotency_key: str = "") -> str:
        action = self._canonical_action(action)
        params = dict(params or {})
        client = configured_tensology_client(source=str(params.pop("source", "decisionsai")))
        mutation_key = idempotency_key or f"decisionsai-{uuid.uuid4()}"
        try:
            if action == "capabilities":
                result = client.get("capabilities")
            elif action == "list_mail":
                result = client.get("mail/messages", params)
            elif action == "read_mail":
                message_id = params.pop("message_id")
                include_thread = self._as_bool(params.pop("include_thread", False))
                query = {"include_thread": "true"} if include_thread else None
                result = client.get(f"mail/messages/{message_id}", query)
            elif action == "read_latest_mail":
                include_thread = self._as_bool(params.pop("include_thread", False))
                params["limit"] = 1
                params["page"] = 1
                listing = client.get("mail/messages", params) or {}
                emails = listing.get("emails") if isinstance(listing, dict) else None
                if not isinstance(emails, list) or not emails:
                    result = {"emails": [], "total": 0}
                else:
                    latest = emails[0] if isinstance(emails[0], dict) else {}
                    message_id = str(
                        latest.get("_id")
                        or latest.get("id")
                        or latest.get("message_id")
                        or latest.get("messageId")
                        or ""
                    ).strip()
                    if not message_id:
                        raise ValueError("Latest mail summary did not include a message ID.")
                    query = {"include_thread": "true"} if include_thread else None
                    result = client.get(f"mail/messages/{message_id}", query)
            elif action == "list_drafts":
                result = client.get("mail/drafts", params)
            elif action == "save_draft":
                result = client.post("mail/drafts", params, idempotency_key=mutation_key)
            elif action == "send_mail":
                if not approved:
                    return "Approval required: show the recipient, subject, and message preview, then ask whether to send it."
                result = client.post("mail/send", params, idempotency_key=mutation_key, approved=True)
            elif action == "list_customers":
                result = client.get("customers", params)
            elif action == "list_projects":
                result = client.get("projects", params)
            elif action == "list_repositories":
                result = client.get("repositories", {"project_id": params["project_id"]})
            elif action == "repository_status":
                repository = self._registered_repository(client, params)
                raw_path = str(repository.get("local_path_hint") or "").strip()
                root = Path(raw_path).expanduser().resolve(strict=False) if raw_path else None
                readable = bool(root and root.is_dir())
                result = client.post(
                    f"repositories/{repository['id']}/status",
                    {
                        "status": "online" if readable else "unavailable",
                        "detail": "Readable local repository" if readable else "Registered local path is unavailable",
                        "machine_name": platform.node(),
                    },
                    idempotency_key=mutation_key,
                )
            elif action == "repository_read_file":
                repository = self._registered_repository(client, params)
                self._authorize_context(client, params, "repository.read_file")
                path = self._inside_repository(self._repository_root(repository), params["path"])
                if not path.is_file():
                    raise ValueError("Requested path is not a file.")
                if path.stat().st_size > 262_144:
                    raise ValueError("File exceeds the 256 KiB read limit.")
                result = {"repository_id": repository["id"], "path": str(path.relative_to(self._repository_root(repository))), "content": path.read_text(encoding="utf-8", errors="replace")}
            elif action in {"repository_search", "repository_find_related_files"}:
                repository = self._registered_repository(client, params)
                operation = "repository.search" if action == "repository_search" else "repository.find_related_files"
                self._authorize_context(client, params, operation)
                query = str(params.get("query") or params.get("message") or "").strip()
                if not query:
                    raise ValueError("query is required.")
                result = self._search_repository(self._repository_root(repository), query)
            elif action == "repository_git_metadata":
                repository = self._registered_repository(client, params)
                self._authorize_context(client, params, "repository.git_metadata")
                root = self._repository_root(repository)
                result = {
                    "repository_id": repository["id"],
                    "branch": self._git(root, "rev-parse", "--abbrev-ref", "HEAD"),
                    "revision": self._git(root, "rev-parse", "HEAD"),
                    "status": self._git(root, "status", "--short").splitlines()[:200],
                }
            elif action == "laya_assess_discussion":
                from distr.core.laya_runtime import assess_request

                message = str(params.get("message") or "").strip()
                ticket_context = str(params.get("ticket_context") or "").strip()
                if not message or not ticket_context:
                    raise ValueError("message and ticket_context are required.")
                message_terms = self._terms(message)
                context_terms = self._terms(ticket_context)
                overlap = sorted(message_terms & context_terms)
                result = {
                    "laya": assess_request(message),
                    "scope_signal": "related" if overlap else "needs_review",
                    "overlap_terms": overlap[:20],
                    "note": "LAYA is advisory; the discussion model must make the final scoped decision.",
                }
            elif action == "sync_project":
                result = client.post("projects", params, idempotency_key=mutation_key)
            elif action == "list_time_entries":
                result = client.get("time-entries", params)
            elif action == "create_time_entry":
                result = client.post("time-entries", params, idempotency_key=mutation_key)
            elif action == "update_time_entry":
                entry_id = params.pop("entry_id")
                result = client.patch(f"time-entries/{entry_id}", params, idempotency_key=mutation_key)
            elif action == "list_invoices":
                result = client.get("invoices", params)
            elif action == "get_invoice":
                result = client.get(f"invoices/{params['invoice_id']}")
            elif action == "create_invoice":
                if not approved:
                    return "Approval required: show the invoice preview, then ask whether to create it."
                result = client.post("invoices", params, idempotency_key=mutation_key, approved=True)
            elif action == "update_invoice":
                if not approved:
                    return "Approval required: show the invoice changes, then ask whether to apply them."
                invoice_id = params.pop("invoice_id")
                result = client.patch(
                    f"invoices/{invoice_id}",
                    params,
                    idempotency_key=mutation_key,
                    approved=True,
                )
            else:
                return f"Unsupported Tensology action: {action}"
            return json.dumps(result, ensure_ascii=False, default=str)
        except KeyError as exc:
            return f"Missing required parameter: {exc.args[0]}"
        except TensologyApiError as exc:
            return f"Tensology error ({exc.code}): {exc}"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return f"Repository context error: {exc}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "thread"}

    @staticmethod
    def _registered_repository(client, params: dict[str, Any]) -> dict[str, Any]:
        project_id = int(params["project_id"])
        repository_id = int(params["repository_id"])
        repositories = client.get("repositories", {"project_id": project_id})
        repository = next((item for item in repositories if int(item.get("id", 0)) == repository_id), None)
        if not repository:
            raise ValueError("Repository is not registered, enabled, and assigned to this machine.")
        return repository

    @staticmethod
    def _authorize_context(client, params: dict[str, Any], operation: str) -> None:
        context_token = str(params.get("context_token") or "").strip()
        if not context_token:
            raise ValueError("A signed ticket context token is required.")
        client.post(
            "context/verify",
            {
                "context_token": context_token,
                "operation": operation,
                "repository_id": int(params["repository_id"]),
            },
            idempotency_key=f"context-{uuid.uuid4()}",
        )

    @staticmethod
    def _repository_root(repository: dict[str, Any]) -> Path:
        raw_path = str(repository.get("local_path_hint") or "").strip()
        if not raw_path:
            raise ValueError("Repository has no local path registered.")
        root = Path(raw_path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Registered repository path is not a directory.")
        return root

    @staticmethod
    def _inside_repository(root: Path, relative_path: Any) -> Path:
        requested = Path(str(relative_path or ""))
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError("Only relative paths inside the registered repository are allowed.")
        resolved = (root / requested).resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Path escapes the registered repository.") from exc
        return resolved

    @staticmethod
    def _search_repository(root: Path, query: str) -> dict[str, Any]:
        terms = TensologyWorkspaceTool._terms(query)
        terms -= {
            "and", "are", "code", "evidence", "exact", "file", "files", "for", "from", "handle",
            "how", "line", "lines", "repository", "scoped", "that", "the", "this", "ticket", "use",
            "what", "when", "where", "which", "with", "you", "your",
        }
        if not terms:
            raise ValueError("query must include searchable words.")
        matches = []
        excluded = {".git", ".venv", ".decisions", ".cursor", "node_modules", "dist", "build", "__pycache__"}
        for directory, names, files in os.walk(root, followlinks=False):
            names[:] = [name for name in names if name not in excluded and not (Path(directory) / name).is_symlink()]
            for filename in files:
                path = Path(directory) / filename
                if path.is_symlink():
                    continue
                relative = str(path.relative_to(root))
                filename_match = terms & TensologyWorkspaceTool._terms(relative)
                snippet_candidates = []
                matched_terms = set(filename_match)
                try:
                    if path.stat().st_size > 1_048_576:
                        continue
                    raw_content = path.read_bytes()
                except OSError:
                    continue
                if b"\0" in raw_content:
                    continue
                try:
                    content = raw_content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                for line_number, line in enumerate(content.splitlines(), 1):
                    line_matches = terms & TensologyWorkspaceTool._terms(line)
                    if line_matches:
                        matched_terms.update(line_matches)
                        snippet_candidates.append((
                            sum(len(term) ** 2 for term in line_matches),
                            {"line": line_number, "text": line.strip()[:240]},
                        ))
                snippet_candidates.sort(key=lambda item: (-item[0], item[1]["line"]))
                snippets = [item[1] for item in snippet_candidates[:3]]
                if filename_match or snippets:
                    matches.append({
                        "path": relative,
                        "snippets": snippets,
                        "score": sum(len(term) ** 2 for term in matched_terms),
                    })
        matches.sort(key=lambda item: (-item["score"], item["path"]))
        for match in matches:
            match.pop("score")
        return {"matches": matches[:100], "truncated": len(matches) > 100}

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=True,
        )
        return completed.stdout.strip()

    @staticmethod
    def _terms(value: str) -> set[str]:
        return {term for term in ''.join(character.lower() if character.isalnum() else ' ' for character in value).split() if len(term) >= 3}
