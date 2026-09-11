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
app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
templates = Jinja2Templates(directory=ROOT / "distr/gui/web/templates")

@app.get("/workflows/")
@app.get("/development/{path:path}")
async def development(request: Request, path: str = ""):
    return templates.TemplateResponse(request, "workflows/studio.html", {"active_page": "/development", "app_version": "fixture", "internal_api_token": "fixture-token"})

@app.get("/api/events/stream")
async def events():
    from fastapi.responses import Response
    return Response(": fixture\n\n", media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8776, log_level="warning")
