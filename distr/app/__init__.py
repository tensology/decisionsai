"""Application package — GUI entry points and the isolated agent worker."""


def __getattr__(name):
    """Lazy import to avoid circular dependency with mixin modules."""
    if name == "run_agent_session":
        from distr.app.agent_worker import run_agent_session

        return run_agent_session
    if name in ("Application", "run"):
        from distr.app.main import Application, run

        return {"Application": Application, "run": run}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
