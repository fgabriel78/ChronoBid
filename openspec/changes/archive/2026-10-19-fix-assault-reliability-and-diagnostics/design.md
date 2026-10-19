# Design

## Context

See proposal.md for the incident and motivation. Relevant current behavior:

- `AuctionOrchestrator._run_assault_for_lot` calls `TacticalBiddingEngine.process_tick_and_decide` once inside the loop and again in a "final consolidation" after the loop. Between both calls `_emit_and_enqueue()` runs `PortfolioOptimizer.refresh_all_ceilings`, which sets `dynamic_ceiling_eur = 0` for non-viable (closed) lots. The second `reconcile_lot_with_ledger` then sees `dynamic_ceiling_eur (0) < next_min_bid_eur` and rewrites `LOST_OTHER` into `LOST_NO_BUDGET`. The dashboard hides this because `effective_ceiling_eur` falls back to `max_bid_eur` when the dynamic ceiling is 0.
- `CatawikiLotPage._solve_press_and_hold` is only invoked from `submit_minimum_bid`. `navigate()` and `extract_snapshot()` never check for a challenge; a full-page PerimeterX block yields "no auction data" → `CatawikiPageLoadError`, which the assault loop retries every 0.25 s without ever resolving the challenge.
- The assault loop already re-evaluates on the next tick after an unconfirmed or failed bid (the reservation is released and the loop continues), so retry exists implicitly but is untested and unlogged.
- `logging` is never configured; the Rich `Live` display owns the terminal. `lot.notes` is overwritten on closure (`apply_market_snapshot` and `reconcile_lot_with_ledger`).
- `leader_phrases` includes the bare phrase `"highest bidder"` and is matched against the whole `body` text.

## Goals / Non-Goals

**Goals:**
- Make the closing outcome deterministic and truthful, independent of how many times a closed lot is reconciled.
- Make a human verification challenge visible and self-healing at every point the bot touches a lot page.
- Leave enough evidence on disk to explain any future loss.

**Non-Goals:**
- Changing the bidding strategy itself (snipe window length, portfolio reserve formula, increment tiers).
- Bypassing or automating PerimeterX beyond the existing press-and-hold procedure.
- Log rotation, remote telemetry or structured log shipping.
- Re-processing past sessions (row 12 history is not recoverable).

## Decisions

### D1. Terminal statuses are sticky; budget loss is attributed from pre-closure evidence

- `reconcile_lot_with_ledger` returns early (after ledger release/confirm) if the lot was already terminal before this call; it never re-classifies a terminal lot.
- `WatchLot` gains a `budget_blocked_at: float | None` marker (monotonic or remaining-seconds snapshot) set by the tactics engine whenever, inside the snipe/overtime window, a due bid is blocked because `next_min > effective_ceiling` (with `next_min <= max_bid_eur`) or `ledger.try_reserve` fails. It is cleared when a bid is subsequently submitted.
- At closure (`apply_market_snapshot` → not leader, closing price within maximum) the status becomes `LOST_NO_BUDGET` iff `budget_blocked_at` is set, otherwise `LOST_OTHER`. The classification happens once, at the transition to closed, inside the model, so the optimizer zeroing the ceiling afterwards has no effect.
- *Alternative considered:* keep the classification in `reconcile_lot_with_ledger` but snapshot the ceiling before `refresh_all_ceilings`. Rejected: still relies on ceiling state at an arbitrary moment and does not capture try_reserve failures; a ceiling lower than `next_min` after closure says nothing about whether a bid was ever due.
- *Alternative considered:* simply drop the final consolidation call. Rejected as the only fix: the final call is still useful to release the ledger when the loop exits via stop or status change; making terminal statuses sticky fixes the class of bug rather than one call site.

### D2. Last meaningful event preserved in notes

- `WatchLot` gains `last_event: str` which is set (not overwritten by routine status notes) for meaningful events: bid submitted / not confirmed / submission error, verification block, ceiling or budget block, assault transient failure.
- On terminal transition, `notes = f"{closing_summary} | Último evento: {last_event}"` when `last_event` is non-empty.
- Routine notes ("Líder provisional", "Superado (actual: …)") keep their current behavior during the auction.

### D3. Verification handling in navigate/extract with a dedicated exception

- New `HumanVerificationBlockedError(CatawikiPageLoadError)` in `catawiki_page.py`.
- `navigate()` calls `_solve_press_and_hold()` after `goto`. `extract_snapshot()` calls it before reading the DOM. If a challenge was detected and is still present after the hold timeout, `extract_snapshot()` raises `HumanVerificationBlockedError`.
- Subclassing `CatawikiPageLoadError` keeps existing "preserve state, retry" behavior in the patrol and assault loops for free; the orchestrator adds an `except HumanVerificationBlockedError` branch before the generic one to set the blocked status.
- *Alternative considered:* a flag on `LotPageSnapshot`. Rejected: a blocked page has no trustworthy market data, so returning a snapshot invites applying stale/zero values to the model.
- Cost: `_find_press_and_hold_target` iterates selectors × frames; on a clean page this is a handful of `count()` calls per tick. Acceptable at the 0.25 s assault tick; if it proves heavy, the check can be restricted to the main frame plus `#px-captcha` on routine reads.

### D4. Verification-blocked status

- New `AuctionStatus.BLOCKED_VERIFICATION = "BLOQUEADO: Verificación Humana"`, non-terminal (`is_active` true). `is_viable` keeps treating it as viable so the assault task continues and patrol does not prune it.
- Set by the orchestrator on `HumanVerificationBlockedError`, also when `submit_minimum_bid` ends with the challenge still present. Cleared implicitly by the next successful `apply_market_snapshot`, which already recomputes status from market data.
- `apply_market_snapshot` must not leave a lot in `BLOCKED_VERIFICATION` when it closes; closure classification follows D1 and `last_event` records the block.

### D5. Pre-snipe challenge check

- In the assault loop, when `seconds_remaining` first crosses `snipe_window_seconds + pre_snipe_check_seconds` (default 10 s), call a new controller method `ensure_no_challenge()` (wrapping `_solve_press_and_hold`) once per crossing (re-armed after an overtime extension pushes time back above the threshold).
- Because D3 already checks before every read, this is mostly a guarantee/log point; it is kept explicit so the protocol (`LotPageControllerProtocol`) and test doubles express it and the log shows it.

### D6. Explicit, tested retry

- No structural change to the loop; add tests and log lines asserting that after an unconfirmed bid or a submission exception the next tick resubmits when still due. Guard: do not resubmit the same amount on the same tick; the next tick re-reads the market so the amount reflects the current `next_min`.

### D7. Leadership detection from user-specific indicators

- Remove the bare `"highest bidder"` phrase; keep only user-specific phrases ("eres el máximo pujador", "you're the highest bidder"...).
- Add `not_leader_phrases` (e.g. "ya no eres el máximo pujador", "you're no longer the highest bidder") which are stripped from the text before matching, so a leader phrase embedded in a negative notice never implies leadership. Implemented as a pure `detect_user_leadership(text)` helper.
- SSR/network leader flags remain as additional sources, unchanged.
- *Alternative considered (initial plan):* match only inside a bid-status container selector. Rejected during implementation: the real container markup could not be verified, and a wrong selector would silently miss genuine leadership, making the bot treat its own lead as outbid and counter-bid against itself (a costlier failure than the false positive being fixed).

### D8. Logging setup

- `cli.py` configures a `logging.FileHandler` at `<excel_dir>/logs/chronobid-YYYYMMDD-HHMMSS.log`, level DEBUG for the `chronobid` logger, root/console handlers left untouched so Rich `Live` is not disturbed. A `--log-dir` option overrides the location; failure to create it prints one warning and continues (spec: Log file not writable).
- A small helper (`chronobid/diagnostics.py`) offers `log_decision(lot, decision, ledger)` that de-duplicates identical consecutive decisions outside the snipe window, keyed per lot id.
- `logs/` added to `.gitignore`.
- The dashboard header shows the active log file path.

## Risks / Trade-offs

- [Press & hold attempts during reads add latency to assault ticks when a challenge is present] → the challenge would block bidding anyway; latency only applies when a challenge exists. Log hold duration to tune `press_and_hold_max_seconds`.
- [Scoped leader detection may miss leadership if Catawiki changes the bid-status markup] → SSR/network leader flags remain as fallback; the ledger already syncs leadership on each read, and logs will show leader=false with own bid present, making the regression visible.
- [`budget_blocked_at` could label a lot "Sin Presupuesto" when the block was early and a later bid simply failed] → the marker is cleared whenever a bid is submitted, so only an unrecovered block counts.
- [Verbose DEBUG logging in long sessions grows the log file] → snipe-window-only per-tick logging plus de-duplication keeps volume small; one file per session.
- [New status value in Excel] → only written, never parsed back as input, so no compatibility impact on reading existing workbooks.

## Migration Plan

- No data migration. Existing workbooks keep working; old rows already labelled "PERDIDO: Sin Presupuesto" are not rewritten.
- Rollback: revert the change; log files can be deleted freely.
