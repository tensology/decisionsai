# Plan — Tensology incoming: three tickets from production

Date: 2026-10-24
Author: mac:pi:decisionsai (pi, DecisionsAI)
Source: Tensology production, `IncomingWorkProposal` rows 9, 32, 36 (`status=pending`, 3 of 38)

## The three items

| # | Proposal | Channel | Project | Amount | Ask |
|---|---|---|---|---|---|
| 36 | Investigate repeated localhost Host-header errors on dev and production | gmail | Merrypak | R412.50 | Find the source of `Host: localhost` requests and correct the host configuration or routing. Do not add `localhost` to production `ALLOWED_HOSTS` without a verified, secure need. |
| 32 | Complete Sprint 3 survey feedback for tickets 67, 69, 71 | mailshot | Player1Sport | R3 300.00 | Implement and verify the survey, report and RPE requirements Martin listed. Prior work was deployed, so confirm current behaviour and close the gaps. |
| 9 | Review 2 Sprint (m) metrics discrepancy | whatsapp | Player1Sport | R550.00 | Investigate why the 2 Sprint (m) metrics are marginally the same, and whether the calculation or the display is wrong. |

## Correlation to local projects

| Ticket | Local repo | Production |
|---|---|---|
| 36 | `/Users/paul/development/WORK/CRYSTALLOGIC/www.merrypak.co.za` | `paul@www.merrypak.co.za`, tree `/home/paul/sites/www.merrypak.co.za` (also `dev.merrypak.co.za`) |
| 32, 9 | `/Users/paul/development/WORK/INTRICODE/www.player1sport.com` | `new.spoortrack.com:22202` (root), tree `/var/www/player1sport.com` |

## Ticket 36 — Merrypak localhost Host-header errors

### Evidence gathered
- Three Django error emails, 21 Oct 2026 13:04:49–53 SAST: `DisallowedHost at /api/basket/count/` (x2) and `/api/shop/gift-vouchers/` (x1).
- Every failure shares `HTTP_HOST = 'localhost'`, `HTTP_USER_AGENT = 'curl/8.5.0'`, `SERVER_PORT = '80'`, `REMOTE_ADDR = ''`, and **no** `X-Forwarded-For`.
- The requests are absent from both the Merrypak nginx access log and the rotated global nginx logs, so nginx never handled them. `REMOTE_ADDR = ''` with no forwarded headers is the signature of a direct call to gunicorn's **unix socket**.
- Occurrence counts: exactly one incident, 4 seconds wide. The production request log has a single matching entry. This is a one-off, not a scanner and not a health check.
- Timing: the production tree picked up `d652cfd8 "Add gift voucher sales switch"` at 21 Oct 13:00. The curls fired four minutes later.
- Source identified in the agent transcript `~/.codex/sessions/2026/10/21/rollout-2026-10-21T12-22-13-*.jsonl`:

  ```
  curl -sS --unix-socket /home/paul/sites/www.merrypak.co.za/sock/gunicorn.sock http://localhost/api/basket/count/
  curl -sS --unix-socket /home/paul/sites/dev.merrypak.co.za/sock/gunicorn.sock http://localhost/api/shop/gift-vouchers/
  ```

  A post-deploy verification step addressed the app through the socket with the URL host `localhost`, so Django's ALLOWED_HOSTS check rejected it and mailed the admins.

- Effective production `ALLOWED_HOSTS` (server `core/settings_local.py`): `127.0.0.1`, `merrypak.co.za`, `dev.merrypak.co.za`, `www.merrypak.co.za`, `new.merrypak.co.za` (variants). **No `localhost`** — correct and to be kept that way.

### Fix
Correct the caller, not the server policy.
1. Add `bin/verify-deployment.sh` to the repo: the one supported way to check dev and prod after a deploy. It talks to the gunicorn socket but always sends the environment's real `Host` header, prints the HTTP status per endpoint, and never uses `localhost` as the host.
2. Document the rule in `docs/` so the next agent does not repeat it.
3. No change to production `ALLOWED_HOSTS`.

### Verification
- Run the script on the box: every endpoint must answer 200, not 400.
- `curl` the same socket with `Host: localhost` must still be rejected (that is the desired behaviour).
- No new `DisallowedHost` entry may appear in the logs.

## Ticket 32 — Player1Sport Sprint 3 (Playwright, tickets 67, 69, 71)

Martin's email, 21 Oct 14:22 UTC. The repository already carries `planning/41-martin-sprint3-mailshot-jira-correlation-2026-10-15.md`, which maps his email to PLAYER1-67 / 69 / 71 and flags PLAYER1-71 as *"retain the normalized readiness formula `(sum - 5) / 20 * 100` unless Martin explicitly approves a different scale"*. His email is that approval: **sum / 25 * 100**.

### Gap analysis and the work

| Requirement | State found | Work |
|---|---|---|
| 67: remove the responses figure on the Participants icon | `SurveyParticipantsList.jsx` shows a `{n} responses` badge per player | Removed the badge |
| 67: deleting a scheduled survey must not affect Reports | `SurveyAPIView.delete` called `survey.delete()`, which cascades to `SurveyResponse` and `SurveyCompletion` — the exact rows the session report reads. A hard delete erased historic Wellness/RPE results | Delete now archives (`archived_at`); responses and completions survive; archived surveys leave the register |
| 67: Wellness result on the report is the one closest to the session date, from before it, per player | Implemented, but ordered by completion time only | Selection now uses the effective result date, so it also honours a chosen past date. Per player, different dates allowed |
| 69: an Org Rep may issue a Wellness survey for a past date; the report closest to that date uses that result | Not implemented: the survey had no date field at all | New `Survey.response_date` field + migration; issue API accepts `response_date` |
| 69: the same applies to RPE surveys | Not implemented | RPE selection falls back to a dated RPE issue whose date is closest to, and before, the session |
| 69: only the Org Rep may do this | Not enforced | The API rejects a past date for any role other than `ORG_REP` (403), and rejects a future date (400) |
| 69: created date in the Survey Register and in Survey Details | Details done 24 Oct (unpushed commit `f3e4c76c`); the register had no created date | Register now shows the created date; the detail view stays |
| 71: RPE % = sum / 25 * 100 | `readiness_percent` used `(sum - 5) / 20 * 100` | Formula changed to `sum / 25 * 100`, rounded to 2 decimals; the locked examples (5, 14, 15, 25) were updated |

### Verification
- Backend: `manage.py test surveys.tests users.tests` — 326 tests pass, including a new `surveys/tests/test_sprint3_survey_closures.py` with 8 tests covering every row of the table above.
- Frontend: `npm test` — 167 of 170 pass; the 3 failures in `sessionReportFormat.test.js` are pre-existing (identical at `HEAD` without this work). `npm run build` succeeds.
- Production: deploy to `/var/www/player1sport.com`, then confirm the migrated column exists, the register shows the created date, and a report reads the new readiness percentage.

## Ticket 9 — Player1Sport "2 Sprint (m)" metrics

Investigate the metric itself: whether the two Sprint (m) values are computed from the same source and rounded into the same figure, and whether the display does the rounding rather than the calculation.

## Execution order

1. Ticket 32 changes, tests, commit, push, deploy, verify.
2. Ticket 36 script, commit, push, deploy, verify.
3. Ticket 9 investigation.
4. Dismiss the three production proposals, with the outcome recorded against each.

## Risk and rules observed

- No outbound messages. The proposals are dismissed in Tensology only after the work is verified.
- Production changes are limited to a code deploy and the migration the deploy runs. No clearing of data, no nginx rewrite, no `ALLOWED_HOSTS` change.
- Every claim above is checked against a file, a log line, a test run or a production query.

---

## Outcome (2026-10-24)

| Proposal | Result | Evidence |
|---|---|---|
| 36 Merrypak | Fixed and deployed | `bin/verify-deployment.sh` added and run on the box: all six checks answer 200. `docs/deployment-verification.md` states the rule. Production `ALLOWED_HOSTS` unchanged. Commits `fdd4e2bb`, `b655e43b`, pushed and pulled to `www.merrypak.co.za` and `dev.merrypak.co.za`. No new `Invalid HTTP_HOST` entry in the log. |
| 32 Player1Sport | Fixed and deployed | `5ad69b21`, pushed and deployed to `/var/www/player1sport.com`. Migration `0008_survey_response_date` applied. Live readiness formula verified (`3x5 -> 60.0`, `1x5 -> 20.0`, `5x5 -> 100.0`), `response_date` column present, register API 200, `Created` in the live bundle, responses badge gone. 326 backend tests pass, 8 of them new. |
| 9 Player1Sport | Fixed and deployed | `1e818eda`, deployed. Live bundle now carries `Sprinting distance (m)` once and `Distance (m)` twice (both genuinely total distance), so the two series can no longer read as one metric. 171 of 171 frontend tests pass; the three pre-existing failures were locale dependence and are fixed. |

**Dismissal.** All three production proposals were dismissed through the same
`decide_proposal` path the UI button uses, actor "Paul (via DecisionsAI)":
36, 32 and 9 moved from `pending` to `dismissed`. Nothing is pending and the
incoming notification badge is cleared.

**Still open for the client.** Two answers went to Martin in the direction of
the requests but are product decisions rather than defects: the exact label
wording for the two sprint-distance measures, and whether an Org Rep may set a
past date on an RPE survey as well as on Wellness (both were implemented the
same way; the backend allows the past date for both kinds, restricted to
`ORG_REP`).
