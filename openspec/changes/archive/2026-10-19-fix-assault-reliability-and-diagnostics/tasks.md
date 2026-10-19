# Tasks

## 1. Truthful closing outcome (D1, D2)

- [x] 1.1 Add `budget_blocked_at: float | None` and `last_event: str` fields to `WatchLot` in `src/chronobid/models/auction.py`; verify `tests/test_models.py` still passes and new fields default to `None`/`""`
- [x] 1.2 Move loss classification into the closing branch of `WatchLot.apply_market_snapshot`: not leader and closing price within maximum → `LOST_NO_BUDGET` iff `budget_blocked_at` is set, else `LOST_OTHER`; append `| Último evento: {last_event}` to the closing notes when present; add model tests for both branches and for note preservation
- [x] 1.3 Make `TacticalBiddingEngine.reconcile_lot_with_ledger` never change the status of a lot that is already terminal (keep ledger release/confirm), and remove the post-closure ceiling-based re-classification; add a test reproducing the row 12 sequence (close in assault → `_emit_and_enqueue` zeroes ceiling → final consolidation) asserting the status stays `PERDIDO`
- [x] 1.4 In `TacticalBiddingEngine.evaluate_lot` / `process_tick_and_decide`, set `budget_blocked_at` and `last_event` when a due bid inside the snipe/overtime window is blocked by `next_min > effective_ceiling` (with `next_min <= max_bid_eur`) or by a failed `try_reserve`, and clear `budget_blocked_at` when a bid decision is returned; verify with tactics tests that a genuine budget block closes as `PERDIDO: Sin Presupuesto`
- [x] 1.5 Set `last_event` in the orchestrator assault loop for bid submitted, bid not confirmed, bid submission error and assault transient failure; verify with an orchestrator e2e test that a closed lot's notes contain the last bid failure

## 2. Human verification handling (D3, D4, D5)

- [x] 2.1 Add `HumanVerificationBlockedError(CatawikiPageLoadError)` and call `_solve_press_and_hold()` at the end of `CatawikiLotPage.navigate()` and at the start of `extract_snapshot()`, raising the new error when the challenge persists after the hold timeout; verify with `tests/test_catawiki_page.py` doubles for: challenge resolved then snapshot read, full-page challenge unresolved → error raised, clean page → no extra behavior
- [x] 2.2 Make `submit_minimum_bid` raise `HumanVerificationBlockedError` if a challenge is still present when it finishes; verify with a page test
- [x] 2.3 Add `AuctionStatus.BLOCKED_VERIFICATION = "BLOQUEADO: Verificación Humana"` (non-terminal, viable) and ensure `apply_market_snapshot` replaces it with the market-derived status on the next valid read; verify with model tests
- [x] 2.4 Handle `HumanVerificationBlockedError` in `run_patrol_step` and `_run_assault_for_lot` before the generic `CatawikiPageLoadError` branch: set the blocked status, notes with remaining seconds, `last_event`, release any reservation for a bid attempt, keep the lot active; verify with orchestrator e2e tests (blocked → shown in Excel row → cleared on next valid snapshot; blocked until close → final `PERDIDO` with verification event in notes)
- [x] 2.5 Add `ensure_no_challenge()` to `LotPageControllerProtocol`, `PlaywrightLotController` and test doubles, and call it once when an assault lot crosses `snipe_window_seconds + pre_snipe_check_seconds` (default 10 s, re-armed after an extension pushes time back above it); verify with an e2e test that the check is invoked exactly once per crossing

## 3. Bid retry and leadership detection (D6, D7)

- [x] 3.1 Add e2e tests asserting that after an unconfirmed bid and after a submission exception inside the snipe window, the next tick resubmits the current `next_min` when still within ceiling and budget, and that the ledger reservation was released in between; adjust the assault loop only if a test reveals a gap
- [x] 3.2 Remove the bare `"highest bidder"` phrase from `CatawikiSelectors.leader_phrases`, add `not_leader_phrases` stripped before matching via a `detect_user_leadership` helper (SSR/network flags unchanged, see revised D7); add tests for generic "highest bidder" text and "no longer highest bidder" notices not producing leadership

## 4. Diagnostic logging (D8)

- [x] 4.1 Create `src/chronobid/diagnostics.py` with `setup_session_logging(excel_path, log_dir=None) -> Path | None` (FileHandler on the `chronobid` logger, DEBUG, no console handler, returns `None` and warns once on failure) and `log_decision(lot, decision, ledger, snipe_window_seconds)` with per-lot de-duplication outside the snipe window; verify with unit tests using `tmp_path` (file created with timestamped name, unwritable dir → `None`, duplicate suppression)
- [x] 4.2 Wire logging into the orchestrator: session start (Excel path, budget, lots), assault entry/exit, per-tick decisions via `log_decision`, bid attempts (amount, duration, challenge solved, confirmed, post-bid state), incidents, and final outcome with `last_event`; verify with an e2e test using `caplog` that the expected records are emitted for a lost-in-assault scenario
- [x] 4.3 Add `--log-dir` to `cli.py`, call `setup_session_logging` before starting the orchestrator, show the log path in the dashboard header, and add `logs/` to `.gitignore`; verify `chronobid --help` lists the option and a dashboard render test includes the path
- [x] 4.4 Document the log location, the new "BLOQUEADO: Verificación Humana" status and the refined "PERDIDO" vs "PERDIDO: Sin Presupuesto" meaning in `README.md`; verify the section exists and matches the implemented names

## 5. Integration check

- [x] 5.1 Run the full test suite (`pytest`) and verify it passes
- [x] 5.2 Run `openspec validate fix-assault-reliability-and-diagnostics --strict` and verify it reports no errors
