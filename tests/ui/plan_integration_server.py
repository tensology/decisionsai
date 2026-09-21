"""Isolated browser fixture: real Plan routes/storage/UI, deterministic model.

Run with the project Python: -m tests.ui.plan_integration_server
Only a temporary database and temporary project files are used.
"""
import contextlib
import json
import io
import os
import time
import argparse
from pathlib import Path
import tempfile

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uvicorn

from distr.core.db import Base
from distr.core.db.projects import Project
from distr.core.db.kanban import KanbanBoard, KanbanLane
from distr.core.planning import assets, build, conversation, service
from distr.gui.web.routes.development.planning import register_routes

ROOT = Path(__file__).resolve().parents[2]
WIRE = '''template Shell
  main
    image "Example logo" asset=1 width=160 height=64
    slot content
screen "Customers" id=customers uses=Shell route=/customers
  content
    heading "Customers"
    form "New customer"
      input "Name" bind=Customer.name
      input "Email" type=email bind=Customer.email
      button "Save" variant=primary
screen "Saved" id=saved uses=Shell route=/saved
  content
    heading "Customer saved"
    link "Back to customers" route=/customers'''


def create_app(folder):
    engine = create_engine(f"sqlite:///{folder / 'plan.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            yield db
    for module in (assets, build, conversation, service):
        module.get_session = session
    assets.UPLOAD_ROOT = folder / "uploads"
    conversation.load_settings_from_db = lambda: {"conversational_llm_provider": "ollama", "conversational_llm_model": "fixture-model"}
    build._global_complexity_route = lambda level: {"backend": "pi", "model": "fixture-model", "model_provider": "ollama"}
    with session() as db:
        db.add(Project(id=1, name="Plan integration", folder_location=str(folder)))
        db.add(KanbanBoard(id=1, name="Plan integration", default_project_id=1))
        db.add(KanbanLane(id=1, board_id=1, name="Backlog"))
        db.commit()
    plan = service.ensure_workspace(board_key="decisions:1", board_provider="decisions", board_name="Plan integration", project_id=1)
    assets.add_asset(plan["id"], name="Example logo.svg", data=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80"><rect width="200" height="80" fill="#ffffff"/><text x="20" y="50" font-size="30" fill="#182b53">EXAMPLE</text></svg>')
    from reportlab.pdfgen import canvas
    pdf_data = io.BytesIO()
    brochure = canvas.Canvas(pdf_data, pagesize=(400, 300))
    for title in ("Example brochure: cover", "Example brochure: details"):
        brochure.setFont("Helvetica", 18)
        brochure.drawString(24, 250, title)
        brochure.showPage()
    brochure.save()
    assets.add_asset(plan["id"], name="Example brochure.pdf", data=pdf_data.getvalue())
    service.apply_agent_edits(plan["id"], edits=[
        {"item_type": "wireframe", "title": "Customers", "content_format": "wire", "content": WIRE},
        {"item_type": "erd", "title": "Customer data", "content_format": "mermaid", "content": "erDiagram\n Customer {\n int id PK\n string name\n string email\n }"},
        {"item_type": "frac", "title": "Customer behavior", "content_format": "markdown", "content": "# Customer behavior\n\nFR-001: Save a valid customer and show confirmation."}],
        expected_revisions={}, instruction="Initialize synthetic test material")
    def stream(provider, model, messages, settings):
        # Opt-in latency for verifying reload/reconnect while a turn is alive.
        time.sleep(min(30, max(0, float(os.environ.get("PLAN_FIXTURE_DELAY", "0")))))
        workspace = service.get_workspace(plan["id"])
        wire = next(item for item in workspace["items"] if item["item_type"] == "wireframe")
        if 'Generate tasks, with edits empty' in messages[0]["content"]:
            task = {"key": "customer", "title": "Implement customer save", "description": "Implement FR-001", "parent_key": None,
                    "depends_on": [], "source_item_ids": [wire["id"]], "acceptance_criteria": ["Customer saves and confirmation appears"],
                    "skills": [], "complexity": "medium", "role": "implementation"}
            yield json.dumps({"reply": "Created the customer tasks.", "edits": [], "tasks": [task,
                {**task, "key": "customer-model", "title": "Persist customer details", "parent_key": "customer"},
                {**task, "key": "customer-form", "title": "Build the customer form", "parent_key": "customer", "depends_on": ["customer-model"]}]})
        else:
            erd = next(item for item in workspace["items"] if item["item_type"] == "erd")
            frac = next(item for item in workspace["items"] if item["item_type"] == "frac")
            yield json.dumps({"reply": "Added the phone field to Customers.", "edits": [{"id": wire["id"], "item_type": "wireframe",
                "title": wire["title"], "content_format": "wire", "expected_revision": wire["revision_count"],
                "content": WIRE.replace('      button "Save"', '      input "Phone" type=tel bind=Customer.phone\n      button "Save"')},
                {"id": erd["id"], "item_type": "erd", "title": erd["title"], "content_format": "mermaid", "expected_revision": erd["revision_count"],
                 "content": "erDiagram\n Customer {\n int id PK\n string name\n string email\n string phone\n }"},
                {"id": frac["id"], "item_type": "frac", "title": frac["title"], "content_format": "markdown", "expected_revision": frac["revision_count"],
                 "content": "# Customer behavior\n\nFR-001: Save a valid customer and show confirmation.\n\nFR-002: Store an optional phone number."}]})
    conversation.runtime.create_stream = lambda provider, model, messages, settings, **scope: stream(provider, model, messages, settings)
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=ROOT / "distr/gui/web/static"), name="static")
    router = APIRouter(prefix="/api")
    register_routes(router, None)
    app.include_router(router)
    @app.get("/renderer-gallery")
    def renderer_gallery():
        return HTMLResponse((ROOT / "tests/ui/wireframe_gallery.html").read_text())
    @app.get("/api/llms/available-providers")
    def providers():
        return {"providers": [{"id": "ollama", "name": "Local fixture"}]}
    @app.get("/api/llms/models")
    def models():
        return {"models": ["fixture-model"]}
    @app.get("/")
    def page():
        return HTMLResponse('''<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Isolated Plan integration</title><link rel="stylesheet" href="/static/development/styles.css?v=plan-routes-2">
        <style>html,body{margin:0;height:100%;background:var(--studio-bg);color:var(--studio-text);font-family:Arial,sans-serif}#plan-root{height:100dvh}button,input,select,textarea{font:inherit;color:inherit;box-sizing:border-box}*{box-sizing:border-box}</style></head>
        <body><main id="plan-root"></main><script type="module">
        import {createPlanConversation} from '/static/development/planning/conversation.js';
        const api = async (path, options={}) => { const response=await fetch('/api'+path,{...options,headers:options.body instanceof FormData?{}:{'Content-Type':'application/json'},body:options.body instanceof FormData?options.body:options.body?JSON.stringify(options.body):undefined}); const data=await response.json(); if(!response.ok)throw Error(data.detail||'Request failed'); return data; };
        const escapeHtml=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        const plan=createPlanConversation({api,escapeHtml,openHome:()=>{},toast:()=>{}},document.querySelector('#plan-root'));
        plan.open(await api('/workflows/studio/plan-workspaces/1'));
        </script></body></html>''')
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", type=int, default=None)
    parser.add_argument("--port", type=int, default=8778)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="decisions-plan-ui-") as location:
        uvicorn.run(create_app(Path(location)), host="127.0.0.1", port=args.port, fd=args.fd, log_level="warning")
