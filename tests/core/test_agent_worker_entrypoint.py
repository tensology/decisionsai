from __future__ import annotations


def test_app_reexports_the_isolated_agent_worker():
    from distr.app import run_agent_session
    from distr.app.agent_worker import run_agent_session as worker_entrypoint

    assert run_agent_session is worker_entrypoint
    assert run_agent_session.__module__ == "distr.app.agent_worker"
