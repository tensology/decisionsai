# Ponytail and Fallow reference assessment

**DecisionsAI integration (2026-06):** Ponytail/Fallow are vendored in `plugins/competition-pack/` and bootstrapped via `distr/core/competition_pack.py`. Workflow `pre_chain` auto-merges ponytail (+ fallow on JS/TS). Cursor projects get `ponytail.mdc` in `.cursor/rules/` during skill provision. Loop preset **Engineering: Implement + Fallow Audit (JS)** (`implement-js-fallow-audit`) adds an explicit fallow audit gate. See also `skills/decisions-harness-stack/SKILL.md`.

Reference clones (sibling to DecisionsAI, same folder as `ecc`, `rtk`):

- `../reference/ponytail` — [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)
- `../reference/fallow` — [fallow-rs/fallow](https://github.com/fallow-rs/fallow)

Vendor slice in-repo: `plugins/competition-pack/` (sync via `scripts/sync_competition_pack.py`).

Bootstrap: `distr/core/competition_pack.py` (mirrors ECC `harness_pack.py` pattern).

---

## What each tool is for

### Ponytail

**Job:** Keep agents from over-building. Before writing code, climb a ladder: YAGNI → stdlib → platform → existing dep → one line → minimum that works.

**Surfaces:** Always-on rules (Cursor `.mdc`, `AGENTS.md`) plus skills (`ponytail`, `ponytail-review`, `ponytail-audit`, `ponytail-debt`, `ponytail-help`). Codex/Claude can use marketplace plugin + lifecycle hooks (needs `node` on PATH).

**Best for:** Implementation steps, refactors, “just add X” tickets where the agent’s default is libraries and abstractions.

**Not for:** Replacing security review, accessibility, or explicit user requests for full implementations.

### Fallow

**Job:** Deterministic JS/TS codebase intelligence — dead code, dupes, complexity, architecture boundaries, PR `audit` verdict. No LLM inside the analyzer; JSON/MCP/LSP for agents.

**Surfaces:** CLI (`npx fallow audit --format json`), npm skill, optional MCP. Python/Django-only repos: skip unless you add a frontend surface.

**Best for:** Pre-merge gates, agent self-check after edits, workflow validation steps on TS/JS projects.

**Not for:** Type checking (`tsc`), lint style, or verified SAST (use dedicated security tools).

---

## What DecisionsAI already does (baseline)

| Layer | ECC | RTK | Ponytail / Fallow (current) |
|-------|-----|-----|-----------------------------|
| Reference clone | `reference/ecc` | `reference/rtk` | `reference/ponytail`, `reference/fallow` |
| Vendored in repo | `plugins/ecc` | — (CLI install) | `plugins/competition-pack` |
| Setup bootstrap | `harness_pack.py` | `setup_project_clis.sh` + `rtk_support.py` | `competition_pack.py` |
| Startup hook | `ensure_harness_pack_setup_quiet` | `verify_agent_harness_setup` | `ensure_competition_pack_setup_quiet` |
| Skill registry | `ecc_vendor` | — | `competition_vendor` |
| Workflow skills | Explicit `pre_chain` / `post_chain` on workflow | Server rewrites shell via RTK | **Auto-prepends** `ponytail` (+ `fallow` on JS/TS) to every `pre_chain` |
| IDE worker skills | ECC harness projection | — | Worker items 11: ponytail + fallow audit before complete |

---

## Integration fit by Decisions surface

### Hermes / orchestrator (desktop, Telegram, remote)

| Capability | Ponytail | Fallow |
|------------|----------|--------|
| System prompt / tool routing | Indirect — agents inherit harness skills after workflow provision | Indirect — same; no `fallow` tool on orchestrator yet |
| Ticket → workflow dispatch | **High** — pre_chain pushes ponytail skill into project harness | **Medium** — only when project has `package.json` / tsconfig |
| Voice / conversational answers | N/A | N/A |
| `ide_thread` / external context | N/A | Could add “run fallow audit on active project” as future tool |

**Gap:** Orchestrator does not run fallow itself; it relies on IDE agents reading the provisioned skill. A dedicated `fallow_audit` system tool would close the loop for non-IDE backends (Pi one-shot).

### Workflows

| Step type | Ponytail | Fallow |
|-----------|----------|--------|
| `send_to_project_cli` / IDE handoff | Skill file in `.cursor/commands` or Pi skills dir | Same |
| `run_command` | No automatic enforcement | **Opportunity:** add `fallow audit` as optional post-step template |
| Validation / gate steps | — | **High fit:** `fallow audit --format json --quiet \|\| true`, fail workflow on verdict `fail` |
| Complexity routing | Complements ECC `tdd-workflow` / `refactor-cleaner` | Complements ECC security/dead-code skills |

**Current behavior:** `provision_workflow_skills` merges `ponytail` (+ `fallow` if JS/TS) into every workflow `pre_chain` even when the workflow JSON is empty. Workflows with their own `pre_chain` keep custom skills after the baseline.

**Recommendation:**

1. Keep ponytail on all implementation `pre_chain` defaults.
2. Add an explicit workflow **template** (not silent merge) for “JS PR gate”: post_chain `["fallow"]` + `run_command` step running audit.
3. Let Python-only workflows opt out via workflow flag `skip_harness_baseline: true` if merge becomes too noisy (not implemented yet).

### Codex

| Mechanism | Status |
|-----------|--------|
| Vendored skills under `~/plugins/decisions-codex/skills/` | Done via bootstrap |
| Official Ponytail marketplace | `codex plugin marketplace add DietrichGebert/ponytail` (install still via Codex `/plugins` UI) |
| `decisions-codex-worker` skill | Requires ponytail + fallow audit before complete |
| Hooks (always-on rules every turn) | Requires Ponytail plugin + trusted hooks in Codex |

**Gap:** Duplicate path — vendored skills vs official plugin. Prefer **official plugin + hooks** for always-on behavior; keep vendored copy as offline fallback and for skill discovery in Decisions registry.

### Cursor

| Mechanism | Status |
|-----------|--------|
| `~/.cursor/rules/decisions-ponytail.mdc` | Done via bootstrap |
| `<project>/.cursor/rules/ponytail.mdc` | Done via `provision_workflow_skills` when ponytail is on pre_chain |
| Skills in plugin / commands | Vendored copy on bootstrap |
| `decisions-cursor-worker` | Requires ponytail + fallow on JS/TS |
| Cursor plugin marketplace for Ponytail | Not used — rules file is the supported Cursor path per upstream docs |

**Gap:** Project-level `.cursor/rules` is auto-copied on workflow start when ponytail is on `pre_chain` (see `push_ponytail_cursor_rule_to_project` in `competition_pack.py`).

### Pi / Claude / other CLIs

Same projection pattern as ECC: skills under `~/.pi/skills/` and `~/.claude/skills/`. Ponytail upstream also documents `pi install git:github.com/DietrichGebert/ponytail` as an alternative.

---

## Comparison to ECC pack (how to think about it)

**ECC** is a large **library** of agents, skills, commands, hooks, MCP configs — projected once, picked per workflow/ticket.

**Ponytail** is a **behavioral constraint** — small ruleset, should be always-on during implementation (like a global rule).

**Fallow** is a **verification tool** — run at boundaries (before complete, before merge), like a CI step agents can call.

```
ECC        → breadth (what workflows exist)
Ponytail   → depth discipline (how little code to write)
Fallow     → evidence (what the repo state actually is)
RTK        → cost (how much shell output hits the model)
```

They stack; they do not replace each other.

---

## Risks and constraints

| Risk | Mitigation |
|------|------------|
| Ponytail too aggressive on legitimate complexity | `ponytail:` comments + `/ponytail lite`; user can steer via orchestrator |
| Fallow false positives on exotic entry points | `fallow init`, baselines, `--save-baseline` on audit |
| Auto pre_chain merge surprises custom workflows | Document; add opt-out flag if needed |
| Fallow not installed | `npx fallow` in skill text; setup runs `npm install -g fallow` on full setup |
| Codex Ponytail hooks need Node | Setup should verify `node` on PATH (same as upstream README) |
| Name “competition-pack” in code | Internal vendor dir name; reference lives next to `rtk`/`ecc` — rename to `harness-tools-pack` optional cleanup |

---

## Recommended next steps (priority order)

1. ~~**Workflow template:** “Implement + audit (JS)” with post-step `fallow audit` and structured result in step output (verdict in `Evidence:`).~~ **Done** — loop preset `implement-js-fallow-audit`.
2. ~~**Project-level Cursor rule push** during skill provision (copy `cursor-ponytail.mdc` → `<project>/.cursor/rules/`).~~ **Done** — `push_ponytail_cursor_rule_to_project`.
3. **Orchestrator tool** `fallow_audit` (or extend `ide_thread`) for Hermes to run audit and speak verdict without IDE.
4. **Workflow opt-out** `skip_harness_baseline` on `AutoWorkflow` when pre_chain merge is wrong for a flow.
5. **Rename** `competition_pack` → `harness_tools_pack` / `plugins/harness-tools` for clarity (cosmetic, large diff).
6. **CI:** GitHub/GitLab workflow snippets in project templates using `fallow-rs/fallow@v2` for repos Decisions manages.

---

## Quick commands

```bash
# Refresh reference + vendor
cd ../reference/ponytail && git pull
cd ../reference/fallow && git pull
cd ../../DecisionsAI && python3 scripts/sync_competition_pack.py

# Bootstrap harness projections
python3 -c "from distr.core.competition_pack import ensure_competition_pack_setup; ensure_competition_pack_setup(run_full=True)"

# Manual fallow check on DecisionsAI frontend
cd frontend && npx fallow audit --format json --quiet || true
```
