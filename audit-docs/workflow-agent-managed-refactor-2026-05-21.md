# Workflow Agent-Managed Refactor State

Date: 2026-05-21

Goal: make workflows a simple ticket execution surface. Users queue tickets, start/observe runs, and tune rules. Internal orchestration, step routing, executor choice, and validation should stay under the hood unless debugging is needed.

## Checklist

- [x] Capture current issue: workflows still expose manual setup/steps as the main experience.
- [x] Capture current issue: CLI/IDE execution can inherit stale step-level project links.
- [x] Simplify workflow UI to Tickets, Runs, and Rules.
- [x] Move run policy into Rules and hide it while active runs lock settings.
- [x] Remove visible internal Hermes branding from the workflow user surface.
- [x] Add a run preview so the user sees ticket, project, complexity, executor, and model before starting.
- [x] Make Send to Project CLI resolve project from run/ticket/board first, with step project only as fallback.
- [x] Add regression coverage proving ticket project overrides stale step project.
- [x] Run syntax and targeted backend tests.
- [x] Browser-check the workflow area for the simplified interaction.

## Product Rule

The workflow should feel like: queue ticket, run ticket, watch work, review evidence. It should not feel like building a process diagram or managing internal agent mechanics.
