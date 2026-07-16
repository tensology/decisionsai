# DecisionsAI single-user release checklist

This checklist is the evidence contract for a production candidate. A release
is not ready because the UI opens; every required item below must have a linked
test report, log, or operator note.

Do not replace a missing real integration with a mocked test. Record the exact
release commit and hosted Actions run in the release notes. A documentation-only
change creates a new release commit and therefore requires a new gate run.

## Automated gates

- [ ] `scripts/verify_runtime.py --json` passes on Python 3.12.8–3.12.x.
- [ ] Default pytest collection completes without errors or unknown markers.
- [ ] The default test suite passes.
- [ ] The ideation → three development tickets → polish acceptance chain passes.
- [ ] The canonical Chromium workflow journey passes at desktop and mobile sizes.
- [ ] Fallow reports no introduced dead code, duplication, or complexity regression.
- [ ] The hosted release gate succeeds on the exact clean commit being released.

## Real integration matrix

- [ ] Telegram text creates or updates a project ticket with correct context.
- [ ] Telegram voice is transcribed and can approve, decline, continue, stop, or steer a run.
- [ ] A Telegram attachment travels outbound and back inbound with its caption and project context intact.
- [ ] Desktop chat can start and steer the same workflow contract.
- [ ] At least one local worker and one remote worker complete a ticket and return normalized artifacts, diagnostics, memory delta, and next actions.
- [ ] Provider timeout, invalid credentials, quota failure, malformed output, and cancellation each produce a useful terminal state.

## Resilience and data safety

- [ ] A long-running local workflow emits heartbeats and leaves the UI responsive.
- [ ] A ten-minute idle soak records probe count, average latency, maximum latency, and zero silent stalls.
- [ ] A physical macOS sleep/wake cycle reconnects Telegram and the web control deck without manual repair.
- [ ] A deliberate network interruption produces a visible reconnect state and recovers without duplicate work.
- [ ] Restart recovery marks orphaned runs clearly and allows safe retry.
- [ ] Forced frozen-app shutdown and subsequent startup leave no DecisionsAI multiprocessing helpers behind.
- [ ] Duplicate Telegram delivery does not duplicate a ticket, approval, or run.
- [ ] Database backup and restore are exercised against a copy of production-shaped data.
- [ ] Schema migration from the last supported release is tested and reversible from backup.
- [ ] Logs and exported reports redact provider keys, tokens, and message secrets.

## Packaging and handoff

- [ ] Fresh-machine macOS install and first launch pass using the documented launcher.
- [ ] Browser binaries, FFmpeg, PortAudio, and model requirements are either installed or reported with a precise remediation message.
- [ ] Release commit is reproducible from a clean tree and CI is green on that exact commit.
- [ ] The release is signed with a valid Developer ID Application identity and notarized using a configured keychain profile.
- [ ] Known limitations and rollback steps are recorded in the release notes.

## Required evidence record

For every checked item, capture enough information for another operator to
reproduce or inspect the result:

- exact 40-character Git commit and clean `main == origin/main` proof;
- hosted release-gate URL plus step conclusions and timestamps;
- pytest/JUnit, Playwright, Fallow, soak, and frozen-app lifecycle reports;
- workflow run IDs and normalized provider/result packets for real workers;
- Telegram interaction run IDs, resolved action, response source, and timestamp;
- database backup path, verification result, restore result, and rollback result;
- `security find-identity -v -p codesigning` result and successful notary history;
- operator note with sleep time, wake time, reconnect evidence, and observed UI state.

## Manual release blockers

Leave the candidate blocked when any item below is true:

- a Telegram interaction is still `pending`, `resolving`, or expired without a replacement proof;
- a provider advertised for the release has missing credentials, quota, or an unverified real execution path;
- no valid Developer ID Application identity or notarization profile is available;
- physical sleep/wake or network recovery has only been simulated;
- the worktree differs from the commit certified by the hosted release gate.
