"""Close-the-loop replies must fit the channel and never promise more than shipped."""

from __future__ import annotations

from distr.core.workflow.reply_channel import (
    append_reply_phase,
    build_reply_instruction,
    channel_profile,
    find_promise_claims,
    humanize_reply,
    normalize_channel,
    resolve_reply_target,
    shape_reply,
)


def test_channel_aliases_map_to_profiles():
    assert normalize_channel("gmail") == "email"
    assert normalize_channel("whats_app") == "whatsapp"
    assert normalize_channel("TG") == "telegram"
    assert normalize_channel("") == "email"
    assert normalize_channel("something-weird") == "email"


def test_whatsapp_target_is_sendable_with_a_thread():
    ticket = {"source_provider": "whatsapp", "source_thread_id": "27634103646"}
    target = resolve_reply_target(ticket)

    assert target.channel == "whatsapp"
    assert target.can_send is True
    assert target.missing == []


def test_missing_provenance_is_reported_not_guessed():
    target = resolve_reply_target({"source_provider": "", "source_thread_id": ""})

    assert target.can_send is False
    assert "source_provider" in target.missing
    assert "source_thread_id" in target.missing


def test_email_target_needs_a_contact():
    ticket = {"source_provider": "gmail", "source_thread_id": "thread-1"}
    target = resolve_reply_target(ticket)

    assert target.channel == "email"
    assert target.can_send is False
    assert "source_contact" in target.missing


def test_whatsapp_reply_has_no_greeting_or_signoff():
    shaped = shape_reply("Fixed the ticket and deployed it.", "whatsapp")

    assert not shaped.lower().startswith("hi")
    assert "Thanks" not in shaped


def test_email_reply_gets_greeting_and_signoff():
    shaped = shape_reply("The invoice rounding is fixed.", "email", contact="Paul Hoft")

    assert shaped.startswith("Hi Paul,")
    assert shaped.rstrip().endswith("Thanks")


def test_email_reply_does_not_double_greet():
    shaped = shape_reply("Hi Paul,\n\nAll done.", "email", contact="Paul Hoft")

    assert shaped.count("Hi Paul") == 1


def test_email_address_contact_does_not_produce_a_greeting_name():
    shaped = shape_reply("All done.", "email", contact="paul@example.com")

    assert shaped.startswith("Hi,")


def test_short_channel_truncates_on_a_word_boundary():
    long_body = "word " * 400
    shaped = shape_reply(long_body, "whatsapp")

    assert len(shaped) <= 703
    assert shaped.endswith("...")


def test_humanize_removes_em_dashes_and_filler():
    raw = "I hope this email finds you well. The fix is in \u2014 ready for review."
    out = humanize_reply(raw)

    assert "\u2014" not in out
    assert "finds you well" not in out
    assert "ready for review" in out


def test_promise_claims_are_detected():
    assert find_promise_claims("It's now live in production.") != []
    assert find_promise_claims("We have deployed the fix.") != []
    assert find_promise_claims("The change is ready for review.") == []


def test_reply_instruction_carries_channel_rules_and_thread():
    ticket = {"source_provider": "whatsapp", "source_thread_id": "abc123", "source_contact": "Paul"}
    text = build_reply_instruction(ticket, result_summary="Fixed the 500.")

    assert "WHATSAPP" in text
    assert "Reply on the existing thread (abc123)" in text
    assert "Fixed the 500." in text
    assert "Only say something shipped" in text


def test_reply_instruction_warns_when_provenance_is_incomplete():
    text = build_reply_instruction({"source_provider": "", "source_thread_id": ""})
    assert "Provenance is incomplete" in text
    assert "needs_input" in text


def test_append_reply_phase_adds_the_block():
    ticket = {"source_provider": "telegram", "source_thread_id": "42"}
    combined = append_reply_phase("Do the work.", ticket)

    assert combined.startswith("Do the work.")
    assert "[REPLY - TELEGRAM]" in combined


def test_channel_profile_defaults_are_sane():
    assert channel_profile("nonsense").key == "email"
    assert channel_profile("whatsapp").max_chars < channel_profile("email").max_chars


def test_tensology_is_its_own_channel_not_email():
    from distr.core.workflow.reply_channel import normalize_channel, resolve_reply_target

    assert normalize_channel("tensology") == "tensology"

    target = resolve_reply_target(
        {"source_provider": "tensology", "source_external_id": "PROJ-169-001"}
    )
    assert target.channel == "tensology"
    assert target.thread_id == "PROJ-169-001"
    # The Tensology integration posts the execution report itself.
    assert target.can_send is False
    assert "handled_by_tensology_integration" in target.missing
