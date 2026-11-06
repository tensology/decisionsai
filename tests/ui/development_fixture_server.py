"""Serve real Development views/assets without starting the agent or scheduler.

Run with python -m tests.ui.development_fixture_server, then set
DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 for fixture browser tests.
"""
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "distr/gui/web/static"), name="static")
app.mount("/workflows/static", StaticFiles(directory=ROOT / "distr/gui/web/static/workflows"), name="workflow_static")
app.mount("/tickets/static", StaticFiles(directory=ROOT / "distr/gui/web/static/kanban"), name="ticket_static")
app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
templates = Jinja2Templates(directory=ROOT / "distr/gui/web/templates")

@app.get("/workflows/")
@app.get("/legacy-workflows/")
@app.get("/legacy-kanban/")
@app.get("/development/{path:path}")
async def development(request: Request, path: str = ""):
    template = {"/legacy-workflows/": "workflows/workflows.html", "/legacy-kanban/": "kanban/kanban.html"}.get(request.url.path, "workflows/studio.html")
    return templates.TemplateResponse(request, template, {"active_page": "/development", "app_version": "fixture", "internal_api_token": "fixture-token"})

@app.get("/api/events/stream")
async def events():
    from fastapi.responses import Response
    return Response(": fixture\n\n", media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8776, log_level="warning")
