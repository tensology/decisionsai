"""Application package — main Application class and its mixins.

Re-exports Application, run_agent_session, and run from distr.app.main
so that ``from distr.app import run`` continues to work.
"""


def __getattr__(name):
    """Lazy import to avoid circular dependency with mixin modules."""
    if name in ("Application", "run_agent_session", "run"):
        from distr.app.main import Application, run_agent_session, run
        return {"Application": Application, "run_agent_session": run_agent_session, "run": run}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
