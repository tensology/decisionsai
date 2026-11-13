"""Development feature routes, composed without changing their public URLs."""
from fastapi import APIRouter
from . import workflows, automations, threads, thread_tools, thread_controls, planning, workflow_execution, workflow_steps, incoming, reports, time_ledger, projection_corrections, thread_ready, open_pencil

def register_routes(router, templates):
    features = APIRouter()
    for module in (workflows, automations, threads, thread_tools, thread_controls, planning, workflow_execution, workflow_steps, incoming, reports, time_ledger, projection_corrections, thread_ready, open_pencil,):
        module.register_routes(features, templates)
    # Static paths must win over workflow identifiers, regardless of module order.
    features.routes.sort(key=lambda route: route.path.count("{"))
    router.include_router(features)
