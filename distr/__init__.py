"""Lazy import so GUI/server-only usage (e.g. bin/start_server) doesn't load agent stack."""

__all__ = ['AgentSession']


def __getattr__(name):
    if name == 'AgentSession':
        from distr.core.agent.session import AgentSession
        return AgentSession
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
