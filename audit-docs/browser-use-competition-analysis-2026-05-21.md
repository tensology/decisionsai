# Browser Use Competition Analysis

Repository pulled to:

`/Users/paul/development/TENSOLOGY/DECISIONS/COMPETITION/browser-use`

Commit inspected:

`5f99e737 chore(llm): default ChatBrowserUse to bu-2-0 (#4876)`

## Summary

Browser Use should not fully replace Playwright in Decisions. It should be added as a new agentic browser execution backend and wired into Hermes.

Playwright is still better for deterministic validation: exact selectors, screenshots, console logs, repeatable assertions, CI tests, and generated validation scripts.

Browser Use is better for exploratory browser work: navigating unfamiliar pages, form filling, authenticated browser sessions, human-like browser tasks, DOM state summaries, multi-step browser plans, and tasks where the agent needs to decide what to click next.

## What Browser Use Provides

- Agentic browser automation through `Agent`.
- Browser sessions through CDP via `BrowserSession`.
- Persistent browser CLI/daemon commands.
- Existing Chrome/profile support through CDP/profile workflows.
- DOM summaries designed for LLM use.
- Built-in browser actions: navigate, click, type, scroll, screenshot, evaluate JS, find elements, dropdowns, file upload, tab management.
- Custom tool/action support.
- Cloud browser option, although Decisions should not require this.
- MIT license.

## Important Risks

- Browser Use is not a pure deterministic test runner.
- It relies on LLM judgment for `Agent` tasks, so it can be nondeterministic.
- It has anonymized telemetry enabled by default via `ANONYMIZED_TELEMETRY=true`; Decisions should set `ANONYMIZED_TELEMETRY=false` for local runs.
- It brings a large dependency surface: OpenAI, Anthropic, Google, Groq, Ollama, Google API libraries, MCP, CDP, PostHog, etc.
- It is Python 3.11+ only.
- It does not remove the need for Playwright when we need exact validation or console/network evidence.

## Decisions Current State

Decisions currently uses Playwright in three different ways:

- Workflow `playwright` step type.
- Workflow validation type `playwright`.
- Main agent `playwright_browser` tool with screenshot and console log capture.

The current Playwright system is code-generation driven. The LLM generates a Playwright script, Decisions runs it in a subprocess, captures stdout/stderr, and in some paths captures screenshot and console evidence.

That is useful for validation but clunky for exploratory browser operation.

## Recommended Architecture

Add Browser Use as a new execution route, not a destructive replacement.

The workflow action types should become:

- `browser_use`: agentic browser task execution.
- `playwright`: deterministic browser script execution and validation.
- `computer_use`: desktop/screen-level automation outside the browser.

Hermes should record all Browser Use activity as executor events:

- `browser_use_session_created`
- `browser_use_step_started`
- `browser_use_action_result`
- `browser_use_screenshot`
- `browser_use_completed`
- `browser_use_failed`

Project execution sessions should support a route like:

- `route_type = "browser_agent"`
- `route_backend = "browser_use"`

The UI should show Browser Use output in the same CLI / IDE / Executor trail surface, not as another separate tab.

## Routing Rule

Use Browser Use when:

- The task is browser-first but not a strict test.
- The page is unfamiliar or dynamic.
- The agent needs to inspect DOM state and decide next actions.
- The task involves form filling, multi-step navigation, or scraping.
- The task benefits from an authenticated profile.

Use Playwright when:

- The workflow needs deterministic pass/fail validation.
- The expected selector, text, route, or screenshot condition is known.
- The ticket requires CI-style browser checks.
- Console/network failures need to be captured as evidence.
- The same check must be repeatable after code changes.

## Suggested Build Plan

- [ ] Add Browser Use dependency behind an optional integration guard.
- [ ] Add `distr/core/browser_use_backend.py` wrapper.
- [ ] Add environment defaults for local safety: `ANONYMIZED_TELEMETRY=false`, cloud disabled unless explicitly configured.
- [ ] Add workflow action type `browser_use`.
- [ ] Add Hermes event emission for Browser Use runs.
- [ ] Add Browser Use execution sessions under `ProjectExecutionSession`.
- [ ] Add workflow settings route policy: low/medium browser tasks can use Browser Use, validation still uses Playwright.
- [ ] Add UI labels: “Browser Agent” instead of exposing Browser Use as raw implementation language.
- [ ] Keep Playwright validation as the final proof layer.

## Decision

Do not swap Playwright out wholesale.

Add Browser Use as the browser-agent executor and keep Playwright as the browser validation engine. This matches the Hermes direction: agentic execution produces activity and evidence, deterministic validation decides whether the ticket actually arrived safely.
