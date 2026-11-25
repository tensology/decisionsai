# Initiative: ambient suggestions from desktop awareness

Date: 2026-11-25
Status: approved for implementation (phases 0-3, settings-gated)
Owner: Paul

## Goal

The agent should notice what the operator is actually doing, assess whether it has
something genuinely useful to say, and occasionally say it in a human way — without
becoming a notification stream.

Success is **not** "more suggestions". Success is: over a week, the operator
welcomes the suggestions it makes and is never annoyed by them. A suggester that
can't shut up gets muted in a day, which means nothing was built.

## Non-goals

- Not a monitoring/surveillance feature. The existing 24h wipe stays.
- Not a reminder system. That already exists (`initiative_scan_*`, tickets, calendar).
- Not a chatty assistant. Silence is the default; speaking is the exception.

## What already exists (do not rebuild)

| Piece | Location | Notes |
|---|---|---|
| **Tier A** — frontmost app + window title | `core/desktop_awareness.py::_tier_a_frontmost_and_title` | NSWorkspace + Quartz, local-only, no sidecar. `TIER_A_BUDGET_S = 0.15` |
| **Tier B** — a11y tree, depth 2 | `::_tier_b_interactive_labels` | sidecar `get_window_tree`; focused element + ≤8 interactive labels. `TOTAL_REFRESH_BUDGET_S = 0.50` |
| Snapshot cache + persistence | `::compact_desktop_snapshot`, `::refresh_desktop_awareness_cache` | stale 5 min, **wiped memory + disk after 24h** (`DELETE_AFTER_S`) |
| Prompt injection | `::get_desktop_inject_block` | already injects the desktop line into the agent prompt |
| Situational spine | `core/initiative/situational.py` | time, idle gaps, handoff, `desktop_for_situational()` |
| Assessment rubric | `core/initiative/rubric.py` | impact / risk / cost |
| Notices + dedup | `core/initiative/notice.py`, `notice_dedup.py` | repeat suppression |
| Delivery channels | `core/initiative/channel_select.py`, `service.py` | Telegram / WhatsApp / own channel |
| Cycle | `core/initiative/service.py` | scans boards/mail/WhatsApp/Telegram |

Currently `initiative_recommendations = 0` (off) and there is **no awareness-depth
setting, no daily budget, and no interrupt threshold**.

## Design

### Model: observe → assess → speak

```
per-screen Tier A/B snapshot            (Phase 0)
        │
        ▼
situational spine (rolling "what is happening")   (Phase 1)
        │
        ▼
assessor: LLM + rubric → {worthy, confidence, kind, text}   (Phase 1)
        │
        ├─ confidence < speak bar        → discard (silence)
        ├─ speak bar ≤ c < interrupt bar → quiet inbox
        └─ c ≥ interrupt bar             → interrupt (chat/voice), if budget allows
                                                  (Phase 2)
```

Two thresholds, one budget:
- **speak bar** — "is this worth saying at all?"
- **interrupt bar** — "is this worth interrupting for?" (much higher)
- **budget** — a backstop so a bad day cannot spam. Not the mechanism.

The assessor is an LLM call over the situation. No regex/keyword gate for
"is this a good idea" — same principle as the work-vs-conversation decision.

### Settings (new)

| Setting | Values | Default | Meaning |
|---|---|---|---|
| `initiative_awareness_depth` | `tier_a`, `tier_ab`, `visual` | `tier_ab` | capture depth; `visual` enables Phase 3 |
| `initiative_awareness_screens` | `focused`, `all` | `all` | per-screen capture scope |
| `initiative_suggestion_budget_per_day` | int | `6` | hard backstop |
| `initiative_interrupt_threshold` | 0..1 | `0.85` | interrupt bar (speak bar is implicit) |
| `initiative_visual_capture_seconds` | int | `900` | Phase 3 only |

`initiative_recommendations` must be `1` for any of this to run.

## Phases

### Phase 0 — Foundation
1. **Per-screen capture.** `_tier_a_frontmost_and_title()` sees one app; add
   `_tier_a_screens()` returning `[{screen, app, title, focused}]` for every screen
   (Quartz `CGWindowListCopyWindowInfo` gives per-window bounds → map to screens).
   Honour `initiative_awareness_screens`.
2. Keep the existing budgets; do not let per-screen capture exceed `TOTAL_REFRESH_BUDGET_S`.
3. Add the settings above; add `desktop_for_situational()` per-screen output.
4. Turn nothing on by default. `initiative_recommendations` stays as the operator set it.

### Phase 1 — Observe → assess → speak (quiet)
5. `initiative/situation_log.py` — bounded rolling log of situations (app/title/focused
   per screen + timestamp), capped in size and age.
6. `initiative/assessor.py` — `assess(situation) -> {worthy, confidence, kind, text}`:
   LLM call with `rubric.py` dimensions + recent situations, JSON out. Fail-closed:
   an assessor error means silence, never a fallback suggestion.
7. `initiative/suggestion_gate.py` — budget + threshold + `notice_dedup`. Pure
   function over `(candidates, budget_state, seen) -> (inbox, interrupts)`.
8. Wire into the existing cycle in `service.py`.

### Phase 2 — Delivery
9. Suggestions inbox surface (non-interrupting) — API + UI.
10. Escalation to chat/voice for interrupts, reusing `channel_select.py`.
11. Per-suggestion actions: dismiss, mute this kind, "don't suggest like this again".

### Phase 3 — Visual tier (opt-in)
12. Multi-screen screenshot capture on `initiative_visual_capture_seconds`.
13. Vision analysis of the capture (reuse the vision path; note the WebP/JSON
    constraints documented in `docs/vision-model-issue.md`).
14. Strictly gated on `initiative_awareness_depth == "visual"`; off by default.

## Error handling

- **Fail-closed everywhere.** Tier A/B already fail open *to empty*; the assessor
  and gate must fail *to silence*. A broken suggester is a silent suggester.
- Capture must never block the initiative cycle: reuse the existing budgets and
  the `try/except → empty` pattern.
- The visual tier must never run when the setting is off, and must respect the
  24h wipe.

## Testing

- Phase 0: per-screen parse against a recorded Quartz window list fixture; budget
  enforcement; `focused` vs `all` scope.
- Phase 1: assessor JSON parsing incl. malformed output (→ silence); gate
  thresholds (below speak / between / above interrupt); budget exhaustion; dedup.
- Phase 2: inbox API contract; escalation only above the interrupt bar.
- Phase 3: capture gated off by default; gated on when set.
- End-to-end: a synthetic situation that clears the interrupt bar produces exactly
  one interrupt and is then dedup'd on repeat.

## Open questions

- Should a dismissed suggestion of a given `kind` suppress that kind for a period?
  (Assumed yes, in Phase 2, via `notice_dedup`.)
- Should the assessor see the *previous* suggestion it made, to avoid repeating
  itself even with different wording? (Assumed yes — feed recent suggestions in.)
