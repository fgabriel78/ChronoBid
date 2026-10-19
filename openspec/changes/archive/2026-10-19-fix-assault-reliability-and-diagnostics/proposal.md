# Proposal

## Why

On 2026-10-19 the agent lost lot row 12 (closed at 320 € against a 350 € maximum) without placing a single bid, and labelled it `PERDIDO: Sin Presupuesto` while 1,432 € were available. Investigation shows three compounding defects: (1) the closing-outcome attribution re-classifies every non-won lot closing in Assault Mode as "no budget" because the dynamic ceiling is zeroed after closure and the lot is reconciled a second time; (2) the PerimeterX "Press & Hold" challenge is only solved inside the bid submission flow, so a challenge raised while opening or reading the assault tab blocks the lot until it closes; (3) there is no persistent log and the last meaningful note is overwritten on closure, so the real cause cannot be determined after the fact. An additional anomaly (row 10 showing an own bid of 1 € on a lot closing days later) points to a possible false-positive leadership detection.

## What Changes

- Fix closing-outcome attribution: `PERDIDO: Sin Presupuesto` is assigned only when, before closure, the budget or dynamic ceiling actually prevented a bid that was otherwise due; terminal lots are never re-classified on later reconciliations.
- Preserve the last meaningful pre-closure event (bid failure, verification block, ceiling block) in the lot notes instead of overwriting it with a generic closing message.
- Add a persistent per-session diagnostic log file capturing patrol/assault ticks, tactical decisions, bid attempts and outcomes, verification challenges and page-load failures.
- Detect and resolve "Press & Hold" human verification during page navigation and snapshot extraction, not only during bid submission.
- Expose a visible non-terminal lot status when a lot is blocked by human verification, in both the dashboard and Excel.
- Account for verification and confirmation latency inside the snipe window: retry an unconfirmed or failed bid while time remains and the lot is still viable.
- Harden leadership detection to avoid false positives from generic page text (e.g. the bare phrase "highest bidder").

## Capabilities

### New Capabilities
- `operational-diagnostics`: persistent per-session diagnostic logging of orchestrator, tactical and browser events, sufficient to reconstruct why a lot was or was not bid on.

### Modified Capabilities
- `catawiki-browser-engine`: Press & Hold resolution extends to navigation and snapshot extraction; new blocked-by-verification state; bid retry within the snipe window; stricter leadership detection.
- `excel-control`: final outcome recording attributes `PERDIDO: Sin Presupuesto` only to real budget/ceiling blocks, adds the generic `PERDIDO` outcome, preserves the last meaningful event in notes, and reports the verification-blocked status.

## Impact

- Code: `src/chronobid/strategy/tactics.py` (reconciliation and outcome attribution), `src/chronobid/orchestrator.py` (assault loop, final consolidation, retries, logging hooks), `src/chronobid/browser/catawiki_page.py` (challenge handling in navigate/extract, leader phrases), `src/chronobid/models/auction.py` (new status, budget-block tracking), `src/chronobid/cli.py` (logging setup, CLI flag for log directory).
- Tests: `tests/test_tactics.py`, `tests/test_orchestrator_e2e.py`, `tests/test_catawiki_page.py`, `tests/test_models.py`, plus new logging tests.
- New runtime artifact: log files written to a `logs/` directory next to the Excel file (git-ignored).
- No new third-party dependencies (standard library `logging`).
