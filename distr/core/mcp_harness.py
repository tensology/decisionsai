"""Unified MCP catalog + non-destructive IDE MCP recalibration on setup/start."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import shutil
from typing import Any

STATE_VERSION = 2

# Legacy Cursor/Codex server names that satisfy the same catalog entry.
_LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    "context7": ("context7-mcp",),
    "exa": ("exa_search", "exa-web-search"),
    "composio": ("composio-connect",),
}

# Deprecated MCP servers — pruned from Cursor mcp.json on recalibrate.
_DEPRECATED_CURSOR_MCP_SERVERS: frozenset[str] = frozenset({"rube", "rube-mcp"})
_DEPRECATED_MCP_URL_MARKERS: tuple[str, ...] = ("rube.app", "rube.composio.dev")


def _home(path: Path | None = None) -> Path:
    return Path(path).expanduser() if path is not None else Path.home()


def _recommendations_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "mcp-recommendations.json"


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "mcp-harness-state.json"


def _cursor_mcp_path(home: Path) -> Path:
    return home / ".cursor" / "mcp.json"


def _codex_config_path(home: Path) -> Path:
    return home / ".codex" / "config.toml"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _env_any(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _substitute_env_mapping(values: dict[str, Any]) -> dict[str, Any]:
    """Replace env placeholders with live values when merging into IDE configs."""
    out: dict[str, Any] = {}
    for key, raw in values.items():
        if isinstance(raw, str):
            if raw.startswith("${env:") and raw.endswith("}"):
                env_name = raw[6:-1]
                out[key] = _env_any(env_name) or raw
            else:
                out[key] = raw
        else:
            out[key] = raw
    return out


def _base_capabilities_mcps() -> dict[str, Any]:
    return {
        "fal_ai_media": {
            "description": "Image, video, and audio generation via fal.ai",
            "auto_merge": True,
            "requires_env": ["FAL_KEY"],
            "api_key_settings_field": "fal_key",
            "cursor_name": "fal-ai",
            "mcp": {
                "command": "npx",
                "args": ["-y", "fal-ai-mcp-server"],
                "env": {"FAL_KEY": "${env:FAL_KEY}"},
            },
            "skill": "fal-ai-media",
        },
        "pixazo_media": {
            "description": "Image, video, TTS, and music via Pixazo (80+ models, one API key)",
            "auto_merge": True,
            "requires_env": ["PIXAZO_API_KEY"],
            "api_key_settings_field": "pixazo_key",
            "api_key_header": "Ocp-Apim-Subscription-Key",
            "cursor_name": "pixazo",
            "mcp": {
                "url": "https://gateway.pixazo.ai/pixazo/mcp",
                "headers": {
                    "Ocp-Apim-Subscription-Key": "${env:PIXAZO_API_KEY}",
                    "Authorization": "Bearer ${env:PIXAZO_API_KEY}",
                },
            },
            "skill": "pixazo-media",
            "docs": "https://www.pixazo.ai/models/mcp",
            "note": "Merged when Pixazo API key is saved in Settings → Third-party. REST/TTS use the same key.",
        },
        "playwright": {
            "description": "Decisions Hermes playwright_browser tool + workflow playwright steps",
            "auto_merge": False,
            "setup": "bin/setup.py installs playwright + chromium in the Decisions venv",
            "skill": "decisions-playwright",
        },
        "browser_use": {
            "description": "Agentic browser automation (Python)",
            "auto_merge": False,
            "setup": "pip install browser-use (Decisions setup.py)",
            "skill": "browser-qa",
        },
        "context7": {
            "description": "Live library documentation (resolve-library-id, query-docs)",
            "auto_merge": True,
            "cursor_name": "context7",
            "skill": "docs-lookup",
            "mcp": {
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp@latest"],
            },
            "setup": "Merged into ~/.cursor/mcp.json and ~/.codex/config.toml on harness recalibrate",
            "note": "Prefer over web search for framework/API docs; optional CONTEXT7_API_KEY for higher limits",
        },
    }


def collect_mcp_catalog() -> dict[str, Any]:
    """Merge MCP entries from all harness packs into one catalog."""
    catalog: dict[str, Any] = {}
    catalog.update(_base_capabilities_mcps())

    try:
        from distr.core.design_reference_pack import design_mcp_recommendations

        for key, value in design_mcp_recommendations().items():
            entry = dict(value)
            entry.setdefault("auto_merge", False)
            catalog[key] = entry
    except Exception:
        pass

    catalog["exa_search"] = {
        "description": "Exa semantic web search (Agent Reach / mcporter)",
        "auto_merge": True,
        "cursor_name": "exa",
        "skill": "agent-reach",
        "mcp": {"url": "https://mcp.exa.ai/mcp"},
        "setup": "Merged into Cursor mcp.json and Codex config.toml when recalibrating",
    }

    catalog["agent_reach_mcporter"] = {
        "description": "Agent Reach mcporter config at ~/.agent-reach/ or config/mcporter.json",
        "auto_merge": False,
        "skill": "agent-reach",
        "setup": "agent-reach install --env=auto",
    }

    catalog["yt_dlp"] = {
        "description": "YouTube/video metadata and subtitles via yt-dlp CLI (Decisions venv)",
        "auto_merge": False,
        "skill": "decisions-yt-dlp",
        "workflow_action": "ytdlp",
        "setup": "pip install yt-dlp (bin/setup.py) — reference clone at ../reference/yt-dlp",
        "note": "YouTube-focused; use bili-cli for Bilibili (agent-reach)",
    }

    catalog["open_design"] = {
        "description": "Open Design local MCP — UI prototypes, decks, motion, hand-drawn diagrams",
        "auto_merge": False,
        "skill": "decisions-open-design",
        "setup": "Install Open Design app; from ../reference/open-design run: od mcp install cursor",
        "note": "Requires Open Design daemon running; complements Decisions Mermaid viewer for technical charts",
        "reference_path": "../reference/open-design",
    }

    try:
        from distr.core.composio_pack import composio_mcp_recommendations

        for key, value in composio_mcp_recommendations().items():
            entry = dict(value)
            entry.setdefault("auto_merge", False)
            catalog[key] = entry
    except Exception:
        pass

    return catalog


def _resolve_env_placeholders(value: str, *, extra_env_names: tuple[str, ...] = ()) -> str:
    if "${env:" not in value:
        return value
    match = re.search(r"\$\{env:([^}]+)\}", value)
    if not match:
        return value
    env_name = match.group(1)
    resolved = _env_any(env_name, *extra_env_names)
    return resolved if resolved else value


def _effective_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Pick token-auth MCP config when credentials are present."""
    effective = dict(entry)
    token_env = effective.get("token_env") or []
    if token_env and any(_env_any(name) for name in token_env):
        alt = effective.get("mcp_with_token")
        if isinstance(alt, dict):
            mcp = dict(alt)
            headers = mcp.get("headers")
            if isinstance(headers, dict):
                mcp["headers"] = {
                    key: _resolve_env_placeholders(str(val), extra_env_names=tuple(token_env))
                    for key, val in headers.items()
                }
            effective["mcp"] = mcp
    return effective


def _cursor_server_block(entry: dict[str, Any], catalog_key: str) -> tuple[str, dict[str, Any]] | None:
    entry = _effective_entry(entry)
    setup = entry.get("setup_commands") or {}
    if isinstance(setup, dict):
        block = setup.get("cursor_mcp_json")
        if isinstance(block, dict) and block:
            name, cfg = next(iter(block.items()))
            if isinstance(cfg, dict):
                return str(name), dict(cfg)

    mcp = entry.get("mcp")
    if not isinstance(mcp, dict):
        return None

    name = str(entry.get("cursor_name") or catalog_key)
    if "url" in mcp:
        cfg: dict[str, Any] = {"url": mcp["url"]}
        if mcp.get("headers"):
            cfg["headers"] = _substitute_env_mapping(mcp["headers"])
        if mcp.get("transport"):
            cfg["transport"] = mcp["transport"]
        return name, cfg

    if "command" in mcp:
        cfg = {"command": mcp["command"]}
        if mcp.get("args"):
            cfg["args"] = list(mcp["args"])
        if mcp.get("env"):
            cfg["env"] = _substitute_env_mapping(mcp["env"])
        settings_field = entry.get("api_key_settings_field")
        required_env = entry.get("requires_env") or []
        if settings_field and required_env and cfg.get("env"):
            from distr.core.third_party_keys import settings_secret

            resolved = settings_secret(*required_env, settings_fields=(settings_field,))
            if resolved:
                for env_name in required_env:
                    if env_name in cfg["env"]:
                        cfg["env"][env_name] = resolved
        return name, cfg

    return None


def _composio_api_key() -> str:
    from distr.core.third_party_keys import composio_api_key, composio_enabled

    if not composio_enabled():
        return ""
    return composio_api_key()


def _apply_composio_api_key(cfg: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Attach x-api-key when Composio credentials are available."""
    key = _composio_api_key()
    if not key or "url" not in cfg:
        return cfg
    header_name = str(entry.get("api_key_header") or "x-api-key")
    out = dict(cfg)
    headers = dict(out.get("headers") or {})
    if not headers.get(header_name):
        headers[header_name] = key
        out["headers"] = headers
    return out


def _pixazo_api_key() -> str:
    from distr.core.third_party_keys import pixazo_api_key, pixazo_enabled

    if not pixazo_enabled():
        return ""
    return pixazo_api_key()


def _apply_pixazo_api_key(cfg: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Inject Pixazo subscription key into MCP HTTP headers when configured."""
    key = _pixazo_api_key()
    if not key or "url" not in cfg:
        return cfg
    out = dict(cfg)
    headers = dict(out.get("headers") or {})
    sub_key = str(entry.get("api_key_header") or "Ocp-Apim-Subscription-Key")
    headers[sub_key] = key
    headers["Authorization"] = f"Bearer {key}"
    out["headers"] = headers
    return out


def _apply_settings_api_key(cfg: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    field = entry.get("api_key_settings_field")
    if field == "pixazo_key":
        return _apply_pixazo_api_key(cfg, entry)
    if entry.get("api_key_env") or field:
        return _apply_composio_api_key(cfg, entry)
    return cfg


def _agent_mcp_block(entry: dict[str, Any], catalog_key: str, *, agent: str) -> tuple[str, dict[str, Any]] | None:
    """Resolve MCP block for cursor vs codex (composio entries may differ by agent URL)."""
    effective = _effective_entry(entry)
    if agent == "codex":
        codex_mcp = effective.get("codex_mcp")
        if isinstance(codex_mcp, dict):
            effective = {**effective, "mcp": codex_mcp}
    block = _cursor_server_block(effective, catalog_key)
    if not block:
        return None
    name, cfg = block
    if entry.get("api_key_env") or entry.get("api_key_settings_field"):
        cfg = _apply_settings_api_key(cfg, entry)
    return name, cfg


def _server_url(cfg: dict[str, Any]) -> str:
    return str(cfg.get("url") or "").strip()


def _server_already_present(name: str, cfg: dict[str, Any], servers: dict[str, Any]) -> bool:
    names = {name, *_LEGACY_ALIASES.get(name, ())}
    if any(alias in servers for alias in names):
        return True

    target_url = _server_url(cfg)
    if not target_url:
        return False

    for existing in servers.values():
        if not isinstance(existing, dict):
            continue
        if _server_url(existing) == target_url:
            return True
    return False


def _codex_section_exists(text: str, name: str) -> bool:
    names = {name, *_LEGACY_ALIASES.get(name, ())}
    for candidate in names:
        if re.search(rf"^\[mcp_servers\.{re.escape(candidate)}\]", text, flags=re.MULTILINE):
            return True
    return False


def _codex_toml_block(name: str, cfg: dict[str, Any]) -> str:
    lines = [f"[mcp_servers.{name}]"]
    if "url" in cfg:
        lines.append(f'url = "{cfg["url"]}"')
        headers = cfg.get("headers") or {}
        if headers:
            lines.append(f"[mcp_servers.{name}.http_headers]")
            for key, value in headers.items():
                lines.append(f'{key} = "{value}"')
        return "\n".join(lines) + "\n"

    if "command" in cfg:
        lines.append(f'command = "{cfg["command"]}"')
        args = cfg.get("args") or []
        lines.append(f"args = {json.dumps(args)}")
        env = cfg.get("env") or {}
        if env:
            lines.append(f"[mcp_servers.{name}.env]")
            for key, value in env.items():
                lines.append(f'{key} = "{value}"')
        return "\n".join(lines) + "\n"

    return ""


def _entry_ready_to_merge(entry: dict[str, Any]) -> bool:
    required = entry.get("requires_env") or []
    if required:
        settings_field = entry.get("api_key_settings_field")
        if settings_field:
            from distr.core.third_party_keys import settings_secret

            if settings_secret(*required, settings_fields=(settings_field,)):
                return True
        return all(_env_any(name) for name in required)
    return True


def _iter_auto_merge_entries(catalog: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for key, entry in catalog.items():
        if not entry.get("auto_merge"):
            continue
        if not _entry_ready_to_merge(entry):
            continue
        items.append((key, entry))
    return items


def _prune_deprecated_cursor_servers(servers: dict[str, Any]) -> list[str]:
    """Remove deprecated Rube MCP entries (product discontinued)."""
    removed: list[str] = []
    for name in list(servers.keys()):
        cfg = servers.get(name)
        if name in _DEPRECATED_CURSOR_MCP_SERVERS:
            del servers[name]
            removed.append(f"removed:{name}")
            continue
        if isinstance(cfg, dict):
            url = str(cfg.get("url") or "")
            if any(marker in url for marker in _DEPRECATED_MCP_URL_MARKERS):
                del servers[name]
                removed.append(f"removed:{name}")
    return removed


def _patch_composio_cursor_servers(servers: dict[str, Any], catalog: dict[str, Any]) -> list[str]:
    """Inject Composio and Pixazo API keys into active MCP server blocks."""
    patches: list[str] = []
    key = _composio_api_key()
    composio_entries = {
        str(entry.get("cursor_name") or catalog_key): entry
        for catalog_key, entry in catalog.items()
        if entry.get("api_key_env") and entry.get("api_key_settings_field") != "pixazo_key"
    }

    for name, entry in composio_entries.items():
        cfg = servers.get(name)
        if not isinstance(cfg, dict) or "url" not in cfg:
            continue

        if key:
            header_name = str(entry.get("api_key_header") or "x-api-key")
            headers = dict(cfg.get("headers") or {})
            if not headers.get(header_name):
                headers[header_name] = key
                cfg["headers"] = headers
                patches.append(f"{name}:headers")

    pixazo_key = _pixazo_api_key()
    pixazo_cfg = servers.get("pixazo")
    if pixazo_key and isinstance(pixazo_cfg, dict) and "url" in pixazo_cfg:
        headers = dict(pixazo_cfg.get("headers") or {})
        if headers.get("Ocp-Apim-Subscription-Key") != pixazo_key:
            headers["Ocp-Apim-Subscription-Key"] = pixazo_key
            headers["Authorization"] = f"Bearer {pixazo_key}"
            pixazo_cfg["headers"] = headers
            patches.append("pixazo:headers")

    return patches


def _merge_cursor_mcp(home: Path, catalog: dict[str, Any]) -> list[str]:
    path = _cursor_mcp_path(home)
    if not path.parent.exists() and not shutil.which("cursor") and not shutil.which("cursor-agent"):
        return []

    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    servers = dict(existing.get("mcpServers") or {})

    pruned = _prune_deprecated_cursor_servers(servers)
    merged_names: list[str] = []
    for key, entry in _iter_auto_merge_entries(catalog):
        block = _agent_mcp_block(entry, key, agent="cursor")
        if not block:
            continue
        name, cfg = block
        if _server_already_present(name, cfg, servers):
            continue
        servers[name] = cfg
        merged_names.append(name)

    patched = _patch_composio_cursor_servers(servers, catalog)
    if not merged_names and not patched and not pruned:
        if path.is_file():
            return []
        return []

    payload = {**existing, "mcpServers": servers}
    _write_json(path, payload)
    return [*merged_names, *patched, *pruned]


def _merge_codex_mcp(home: Path, catalog: dict[str, Any]) -> list[str]:
    path = _codex_config_path(home)
    if not path.is_file():
        return []

    text = path.read_text(encoding="utf-8")
    merged_names: list[str] = []
    append_blocks: list[str] = []

    for key, entry in _iter_auto_merge_entries(catalog):
        block = _agent_mcp_block(entry, key, agent="codex")
        if not block:
            continue
        name, cfg = block
        if _codex_section_exists(text, name):
            continue
        toml = _codex_toml_block(name, cfg)
        if not toml:
            continue
        append_blocks.append(toml)
        merged_names.append(name)

    if not append_blocks:
        return []

    if not text.endswith("\n"):
        text += "\n"
    text += "\n# DecisionsAI harness MCP auto-merge\n" + "".join(append_blocks)
    path.write_text(text, encoding="utf-8")
    return merged_names


def _fingerprint(catalog: dict[str, Any], cursor_merged: list[str], codex_merged: list[str]) -> str:
    raw = json.dumps(
        {
            "catalog_keys": sorted(catalog.keys()),
            "cursor_merged": sorted(cursor_merged),
            "codex_merged": sorted(codex_merged),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def recalibrate_mcp_harness(
    *,
    home: Path | None = None,
    run_full: bool = False,
) -> dict[str, Any]:
    """Rewrite MCP catalog and lightly merge auto_merge servers into Cursor and Codex."""
    _ = run_full  # reserved; env-gated entries only merge when credentials exist
    try:
        from distr.core.third_party_keys import sync_third_party_env_keys

        sync_third_party_env_keys()
    except Exception:
        pass
    base_home = _home(home)
    catalog = collect_mcp_catalog()
    _write_json(_recommendations_path(base_home), catalog)

    cursor_merged = _merge_cursor_mcp(base_home, catalog)
    codex_merged = _merge_codex_mcp(base_home, catalog)
    fingerprint = _fingerprint(catalog, cursor_merged, codex_merged)

    payload = {
        "state_version": STATE_VERSION,
        "status": "configured",
        "fingerprint": fingerprint,
        "catalog_path": str(_recommendations_path(base_home)),
        "cursor_mcp_path": str(_cursor_mcp_path(base_home)),
        "codex_config_path": str(_codex_config_path(base_home)),
        "cursor_merged": cursor_merged,
        "codex_merged": codex_merged,
        "catalog_count": len(catalog),
    }
    _write_json(_state_path(base_home), payload)
    return payload


def recalibrate_mcp_harness_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_MCP_HARNESS_RECALIBRATE") or "").strip() == "1":
        return
    try:
        recalibrate_mcp_harness(run_full=False)
    except Exception:
        pass
