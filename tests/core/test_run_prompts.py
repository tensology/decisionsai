from distr.core.workflow import run_prompts as rp


def test_budget_summary_leads_with_turns_and_dollars():
    text = rp.budget_summary({"turns_used": 41, "max_turns": 80, "estimated_cost_usd": 0.0213,
                              "tokens_used": 120000, "max_tokens": 100000})
    assert text.startswith("Run used 41 of 80 turns and about $0.02.")
    assert "Tokens: 120,000 of 100,000." in text


def test_budget_interrupt_helper_removed():
    assert not hasattr(rp, "budget_interrupt")
    assert not hasattr(rp, "budget_recommendation")
    assert "budget" not in rp.WAIT_TEMPLATES


def test_templates_are_distinct_and_complete():
    kinds = ["restart_recovery", "provider_failure", "human_question"]
    texts = {rp.wait_prompt(k, "x happened", label="P T") for k in kinds}
    assert len(texts) == 3
    for t in texts:
        assert "Continue" in t and "Safe default if you do nothing:" in t


def test_wrappers_idempotent():
    once = rp.provider_failure_prompt("Pi failed. Use Codex?", label="A")
    assert rp.provider_failure_prompt(once) == once
    q = rp.human_question_prompt("Which colour?", label="A")
    assert "Which colour?" in q and rp.human_question_prompt(q) == q


def test_restart_prompt_mentions_limit():
    assert "resumed itself 2 time" in rp.restart_prompt("A", attempts=2, limit=2)


def test_provider_swap_record_replaces_rationale():
    rec = rp.provider_swap_record(failed_backend="pi", failed_model="m1", to_backend="codex",
                                  to_model="auto", preflight={"failure_reason": "HTTP 503"},
                                  approved_by="user:1")
    assert "HTTP 503" in rec["rationale"] and "user:1" in rec["rationale"]
    assert "inherited" not in rec["rationale"]
    assert rp.provider_swap_record(failed_backend="pi", failed_model="m", to_backend="c",
                                   to_model="a")["approved_by"] == "unknown approver"
