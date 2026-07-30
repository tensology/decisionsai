"""Preferences API for Monk Mode website blocking."""

from typing import Any

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from distr.core.monk_mode import get_monk_mode_service

from ._shared import route_handler


class MonkSiteInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    label: str = Field(default="", max_length=80)


class MonkToggleInput(BaseModel):
    enabled: bool


class MonkScheduleWindowInput(BaseModel):
    id: str | None = None
    days: list[int]
    start: str
    end: str
    enabled: bool = True


class MonkScheduleInput(BaseModel):
    enabled: bool
    windows: list[MonkScheduleWindowInput] = Field(default_factory=list)


def _model_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def register_routes(router, templates):

    @router.get("/monk")
    @route_handler("load Monk Mode")
    async def get_monk_mode():
        return await run_in_threadpool(get_monk_mode_service().get_state)

    @router.post("/monk/sites")
    @route_handler("add Monk Mode website", status_code=400)
    async def add_monk_site(data: MonkSiteInput):
        return await run_in_threadpool(get_monk_mode_service().add_site, _model_dict(data))

    @router.put("/monk/sites/{site_id}")
    @route_handler("update Monk Mode website", status_code=400)
    async def update_monk_site(site_id: str, data: MonkSiteInput):
        return await run_in_threadpool(get_monk_mode_service().update_site, site_id, _model_dict(data))

    @router.delete("/monk/sites/{site_id}")
    @route_handler("remove Monk Mode website", status_code=400)
    async def remove_monk_site(site_id: str):
        return await run_in_threadpool(get_monk_mode_service().remove_site, site_id)

    @router.post("/monk/toggle")
    @route_handler("toggle Monk Mode", status_code=400)
    async def toggle_monk_mode(data: MonkToggleInput):
        return await run_in_threadpool(get_monk_mode_service().set_enabled, data.enabled)

    @router.put("/monk/schedule")
    @route_handler("save Monk Mode schedule", status_code=400)
    async def save_monk_schedule(data: MonkScheduleInput):
        windows = [_model_dict(window) for window in data.windows]
        return await run_in_threadpool(get_monk_mode_service().set_schedule, data.enabled, windows)

