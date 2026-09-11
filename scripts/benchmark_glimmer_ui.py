#!/usr/bin/env python3
"""Run a reproducible, Glimmer-only product UI coding benchmark."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from distr.core.capabilities_pack import merge_harness_pre_chain
from distr.core.project_cli_backends.base import ProjectTask
from distr.core.project_cli_backends.registry import get_backend
from distr.core.workflow.skill_provision import provision_workflow_skills


EDITABLE = {"index.html", "styles.css", "app.js"}
REQUIRED_SKILLS = {
    "decisions-harness-stack",
    "decisions-headroom",
    "agent-watchdog",
    "ponytail",
    "impeccable",
    "browser-qa",
    "decisions-playwright",
    "frontend-design-direction",
}

FILES = {
    "package.json": '{"name":"northstar-ui-benchmark","private":true,"scripts":{"test":"python -m unittest discover -s tests -v"}}\n',
    "AGENTS.md": """# Glimmer UI benchmark

- Use RTK for shell commands when it is available.
- Read the projected Pi skills named in the task before editing.
- Modify only index.html, styles.css, and app.js.
- Keep the implementation dependency-free and do not use remote assets.
- Run the supplied unit tests before finishing.
""",
    "index.html": """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Northstar Run Control</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar" aria-label="Project navigation">
      <div class="brand">Northstar</div>
      <button id="new-run" type="button">New run</button>
      <nav aria-label="Primary"><a href="#overview">Overview</a><a href="#runs" aria-current="page">Runs</a><a href="#reports">Reports</a></nav>
      <div class="project-list"><p>Projects</p><button type="button" data-project="DecisionsAI">DecisionsAI</button><button type="button" data-project="Player1Sport">Player1Sport</button><button type="button" data-project="AuctionNow">AuctionNow</button></div>
    </aside>
    <main id="runs">
      <header class="topbar">
        <button id="nav-toggle" type="button" aria-label="Toggle project navigation" aria-expanded="true">Menu</button>
        <div><h1>Run control</h1><p>Monitor agents, inspect checkpoints, and intervene when work needs judgment.</p></div>
        <span id="system-health">Systems nominal</span>
      </header>
      <section class="toolbar" aria-label="Run controls">
        <label for="run-search">Search runs</label><input id="run-search" type="search" placeholder="Ticket, project, or run ID">
        <div role="group" aria-label="Filter runs"><button type="button" data-filter="all" aria-pressed="true">All</button><button type="button" data-filter="active" aria-pressed="false">Active</button><button type="button" data-filter="waiting" aria-pressed="false">Waiting</button><button type="button" data-filter="failed" aria-pressed="false">Failed</button></div>
        <div role="group" aria-label="Change view"><button type="button" data-view="board" aria-pressed="true">Board</button><button type="button" data-view="list" aria-pressed="false">List</button></div>
      </section>
      <div class="workspace">
        <section class="run-area" aria-labelledby="run-heading"><div class="section-heading"><h2 id="run-heading">Live workflow runs</h2><span id="result-count"></span></div><div id="run-grid" aria-live="polite"></div><div id="empty-state" hidden><h3>No matching runs</h3><p>Clear the search or choose another status.</p><button type="button" id="clear-filters">Clear filters</button></div></section>
        <aside class="inspector" aria-labelledby="inspector-title"><p>Selected run</p><h2 id="inspector-title">Choose a run</h2><div id="inspector-body"><p>Select a workflow to see its checkpoints and controls.</p></div></aside>
      </div>
    </main>
  </div>
  <dialog id="cancel-dialog"><form method="dialog"><h2>Cancel this run?</h2><p>The agent will stop after its current safe checkpoint.</p><menu><button value="close">Keep running</button><button id="confirm-cancel" value="confirm">Cancel run</button></menu></form></dialog>
  <div id="toast" role="status" aria-live="polite"></div>
  <script src="app.js"></script>
</body>
</html>
""",
    "styles.css": """* { box-sizing: border-box; }
body { margin: 0; font-family: sans-serif; }
.app-shell { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; }
.sidebar { padding: 20px; border-right: 1px solid #ccc; }
.sidebar nav, .project-list { display: grid; gap: 8px; margin-top: 24px; }
main { padding: 24px; }
.topbar, .toolbar, .section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.workspace { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 24px; margin-top: 24px; }
#run-grid { display: grid; gap: 12px; }
.run-card { border: 1px solid #ccc; padding: 16px; }
.run-card button { width: 100%; text-align: left; }
.inspector { border-left: 1px solid #ccc; padding-left: 24px; }
@media (max-width: 720px) { .app-shell { grid-template-columns: 1fr; } .sidebar { display: none; } .workspace { grid-template-columns: 1fr; } }
""",
    "app.js": """const runs = [
  {id:'RUN-2841', ticket:'DEV-431', project:'DecisionsAI', title:'Harden automation recovery states', status:'active', agent:'Glimmer', elapsed:'12m', progress:68, checkpoint:'Browser verification', steps:['Scope confirmed','Backend tests passed','UI review running']},
  {id:'RUN-2838', ticket:'PLY-169', project:'Player1Sport', title:'Configure wellness survey due dates', status:'waiting', agent:'Glimmer', elapsed:'31m', progress:52, checkpoint:'Waiting for approval', steps:['Data model traced','Migration prepared','Approval required']},
  {id:'RUN-2835', ticket:'AUC-88', project:'AuctionNow', title:'Repair bidder notification retry', status:'failed', agent:'Muse', elapsed:'8m', progress:34, checkpoint:'Integration test failed', steps:['Queue inspected','Retry patched','Test failed']},
  {id:'RUN-2832', ticket:'DEV-422', project:'DecisionsAI', title:'Add project folder drop target', status:'active', agent:'Glimmer', elapsed:'6m', progress:23, checkpoint:'Implementing sidebar handler', steps:['Contract mapped','Implementation running']}
];
let filter='all', query='', selected=null, view='board';
const grid=document.querySelector('#run-grid'), empty=document.querySelector('#empty-state'), count=document.querySelector('#result-count');
function visibleRuns(){return runs.filter(run => (filter==='all'||run.status===filter) && `${run.id} ${run.ticket} ${run.project} ${run.title}`.toLowerCase().includes(query));}
function render(){const items=visibleRuns(); count.textContent=`${items.length} runs`; empty.hidden=items.length>0; grid.hidden=!items.length; grid.dataset.view=view; grid.innerHTML=items.map(run=>`<article class="run-card" data-status="${run.status}"><button type="button" data-run="${run.id}"><span>${run.project} / ${run.ticket}</span><strong>${run.title}</strong><span>${run.status} · ${run.elapsed} · ${run.progress}%</span><progress max="100" value="${run.progress}">${run.progress}%</progress></button></article>`).join(''); document.querySelectorAll('[data-run]').forEach(button=>button.addEventListener('click',()=>selectRun(button.dataset.run)));}
function selectRun(id){selected=runs.find(run=>run.id===id); document.querySelector('#inspector-title').textContent=selected.title; document.querySelector('#inspector-body').innerHTML=`<dl><dt>Run</dt><dd>${selected.id}</dd><dt>Agent</dt><dd>${selected.agent}</dd><dt>Checkpoint</dt><dd>${selected.checkpoint}</dd></dl><ol>${selected.steps.map(step=>`<li>${step}</li>`).join('')}</ol><button type="button" id="cancel-run" ${selected.status==='failed'?'disabled':''}>Cancel run</button>`; const cancel=document.querySelector('#cancel-run'); if(cancel) cancel.addEventListener('click',()=>document.querySelector('#cancel-dialog').showModal());}
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(item=>item.setAttribute('aria-pressed',String(item===button))); render();}));
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{view=button.dataset.view; document.querySelectorAll('[data-view]').forEach(item=>item.setAttribute('aria-pressed',String(item===button))); render();}));
document.querySelector('#run-search').addEventListener('input',event=>{query=event.target.value.trim().toLowerCase(); render();});
document.querySelector('#clear-filters').addEventListener('click',()=>{filter='all';query='';document.querySelector('#run-search').value='';render();});
document.querySelector('#nav-toggle').addEventListener('click',event=>{const shell=document.querySelector('.app-shell'); shell.classList.toggle('nav-closed'); event.currentTarget.setAttribute('aria-expanded',String(!shell.classList.contains('nav-closed')));});
document.querySelector('#confirm-cancel').addEventListener('click',()=>{document.querySelector('#toast').textContent=`${selected?.id||'Run'} cancellation requested`;});
render();
""",
    "tests/test_contract.py": """import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class UIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html=(ROOT/'index.html').read_text()
        cls.css=(ROOT/'styles.css').read_text()
        cls.js=(ROOT/'app.js').read_text()

    def test_landmarks_and_dialog_remain(self):
        for token in ('<main', '<nav', '<aside', '<dialog'):
            self.assertIn(token, self.html)
    def test_search_has_programmatic_label(self):
        self.assertRegex(self.html, r'<label[^>]+for=[\"\\\']run-search')
    def test_live_regions_remain(self):
        self.assertGreaterEqual(self.html.count('aria-live='), 2)
    def test_interactions_remain(self):
        for token in ('data-filter', 'data-view', 'run-search', 'nav-toggle', 'cancel-dialog'):
            self.assertIn(token, self.js + self.html)
    def test_responsive_breakpoint_exists(self):
        self.assertRegex(self.css, r'@media\\s*\\([^)]*max-width')
    def test_focus_visible_is_authored(self):
        self.assertIn(':focus-visible', self.css)
    def test_reduced_motion_is_handled(self):
        self.assertIn('prefers-reduced-motion', self.css)
    def test_design_tokens_exist(self):
        self.assertGreaterEqual(len(re.findall(r'--[a-zA-Z][\\w-]*\\s*:', self.css)), 8)
    def test_no_remote_dependencies(self):
        self.assertNotRegex(self.html + self.css + self.js, r'https?://')
    def test_no_unicode_icon_substitutes(self):
        self.assertNotRegex(self.html, r'[😀-🙏]')

if __name__ == '__main__': unittest.main()
""",
}

PROMPT = """Build and polish the existing Northstar Run Control product UI.

This is a Glimmer-only UI benchmark. Read `.pi/skills/ponytail/SKILL.md` and `.pi/skills/impeccable/SKILL.md` before editing. For Impeccable, this existing fixture is already fully briefed: use its Operate-mode and craft-floor guidance, do not start an interview or critique workflow. Browser QA and frontend design guidance are projected for reference, but the supplied requirements below are authoritative. Use RTK for shell commands when available.

Design direction:
- Operate-mode developer workspace for frequent use, not a marketing page.
- Calm, dense, decisive dark interface with one restrained amber accent.
- Moderate visual variance, low motion, high information density.
- Product-specific hierarchy for projects, workflow runs, checkpoints, and intervention.
- Avoid generic purple AI gradients, glass effects, excessive rounded cards, decorative charts, emoji icons, and remote assets.

Requirements:
1. Preserve all supplied behaviors: status filters, search, board/list toggle, run selection, inspector, cancel confirmation, clear filters, and collapsible navigation.
2. Make every state legible: active, waiting, failed, selected, empty, hover, focus, disabled, dialog, and toast.
3. Create a coherent token system in CSS and use semantic HTML. Meet WCAG AA contrast, 44px mobile targets, keyboard focus visibility, and reduced-motion handling.
4. Make it excellent at 1440px, 768px, and 375px with no horizontal overflow. On mobile, project navigation must remain reachable instead of disappearing permanently.
5. Keep it dependency-free. Do not add files, remote fonts, remote images, or build steps.
6. Modify only index.html, styles.css, and app.js. Do not modify AGENTS.md, package.json, tests, projected skills, or any other file.
7. Run `python -m unittest discover -s tests -v` and report the result.

Do not merely describe a design. Implement it fully in the three allowed files.
"""


@dataclass(frozen=True)
class Model:
    name: str = "muse-glimmer-30b-mlx-ui"
    backend: str = "pi"
    provider: str = "ollama"
    model: str = "muse-glimmer:30b-mlx"


def write_workspace(folder: Path) -> None:
    for relative, content in FILES.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (folder / "prompt.txt").write_text(PROMPT, encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def static_grade(folder: Path, originals: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=folder, capture_output=True, text=True, timeout=60, check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    passed = sum(line.rstrip().endswith("... ok") for line in output.splitlines())
    changed = sorted(name for name in EDITABLE if digest(folder / name) != originals[name])
    protected = []
    known = set(FILES) | {"prompt.txt"}
    for relative in sorted(known - EDITABLE - {"prompt.txt"}):
        path = folder / relative
        if not path.is_file() or digest(path) != originals[relative]:
            protected.append(relative)
    unexpected = sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.is_file()
        and ".pi" not in path.parts
        and ".impeccable" not in path.parts
        and "__pycache__" not in path.parts
        and path.relative_to(folder).as_posix() not in known
        and not path.name.endswith((".pyc", ".pyo"))
    )
    css = (folder / "styles.css").read_text(encoding="utf-8")
    html = (folder / "index.html").read_text(encoding="utf-8")
    js = (folder / "app.js").read_text(encoding="utf-8")
    quality = {
        "all_editable_files_changed": set(changed) == EDITABLE,
        "css_token_system": css.count("--") >= 16 and ":root" in css,
        "focus_visible": ":focus-visible" in css,
        "reduced_motion": "prefers-reduced-motion" in css,
        "responsive_rules_present": "max-width" in css and "44px" in css,
        "semantic_dialog": "<dialog" in html and "showModal" in js,
        "search_filter_view_contract": all(token in js for token in ("data-filter", "data-view", "run-search")),
        "mobile_navigation_reachable": "nav-closed" in css and "nav-toggle" in js,
        "no_remote_dependencies": "http://" not in (html + css + js) and "https://" not in (html + css + js),
        "no_inline_style_sprawl": html.count("style=") == 0,
    }
    return {
        "score": passed + sum(quality.values()) + (5 if not protected and not unexpected else 0),
        "max_score": 25,
        "tests": {"passed": passed, "expected": 10, "exit_code": completed.returncode, "output": output},
        "quality_checks": quality,
        "scope": {"changed": changed, "protected_changes": protected, "unexpected_files": unexpected},
    }


async def run(folder: Path, timeout: int) -> dict[str, Any]:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    write_workspace(folder)
    originals = {relative: digest(folder / relative) for relative in FILES}
    requested = ["ponytail", "impeccable", "browser-qa", "frontend-design-direction"]
    merged = merge_harness_pre_chain(requested, project_folder=str(folder))
    workflow = SimpleNamespace(pre_chain=json.dumps(requested), post_chain="[]", id=99001)
    projected = provision_workflow_skills(workflow=workflow, project_folder=str(folder), backend_id="pi")
    task = ProjectTask(
        project_id=99001, project_name=folder.name, folder=str(folder), instruction=PROMPT,
        origin="benchmark", model=Model.model, ticket_complexity="high", codex_reasoning_effort="medium",
        adapter_options={"model_provider": Model.provider, "mutation_expected": True, "timeout_seconds": timeout},
    )
    events: list[dict[str, Any]] = []
    started = time.monotonic()
    result = await get_backend(Model.backend).send_task(task, on_event=lambda event: events.append(event))
    elapsed = round(time.monotonic() - started, 3)
    return {
        "benchmark": "glimmer-product-ui",
        "model": Model.model,
        "success": result.success,
        "elapsed_seconds": elapsed,
        "engine": result.engine,
        "error": result.error,
        "output": result.output,
        "event_count": len(events),
        "harness": {
            "merged_pre_chain": merged,
            "projected": projected,
            "required_present": sorted(REQUIRED_SKILLS.intersection(projected)),
            "missing_required": sorted(REQUIRED_SKILLS.difference(projected)),
        },
        "static_grade": static_grade(folder, originals),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="artifacts/model-benchmark-glimmer-ui")
    parser.add_argument("--timeout", type=int, default=1500)
    parser.add_argument("--grade", help="Only grade an existing generated workspace")
    args = parser.parse_args()
    if args.grade:
        folder = Path(args.grade).expanduser().resolve()
        originals_dir = folder.parent / ".original-ui-fixture"
        if originals_dir.exists():
            originals = {relative: digest(originals_dir / relative) for relative in FILES}
        else:
            originals = {relative: hashlib.sha256(content.encode()).hexdigest() for relative, content in FILES.items()}
        print(json.dumps(static_grade(folder, originals), indent=2))
        return 0
    output = Path(args.output_dir).expanduser().resolve()
    workspace = output / Model.name
    originals_dir = output / ".original-ui-fixture"
    if originals_dir.exists():
        shutil.rmtree(originals_dir)
    originals_dir.mkdir(parents=True)
    write_workspace(originals_dir)
    payload = asyncio.run(run(workspace, args.timeout))
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
