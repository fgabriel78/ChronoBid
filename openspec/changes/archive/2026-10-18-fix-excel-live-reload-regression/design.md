# Design

## Context

See `proposal.md` for motivation. Currently:
1. `extract_input_signature()` in `src/chronobid/excel/workbook_io.py` opens the Excel file with `load_workbook(target, data_only=True, read_only=True)` and iterates `for r in range(FIRST_DATA_ROW, max_row + 1)` using `ws.max_row` and `ws.cell(row=r, column=c)`. In `openpyxl`, `ReadOnlyWorksheet.max_row` is parsed exclusively from the `<dimension ref="..."/>` XML element in `xl/worksheets/sheet1.xml` rather than scanning actual `<row>` elements. When a spreadsheet editor saves new rows without updating `<dimension>` (or writes an unsized dimension), `extract_input_signature()` ignores the newly added rows and returns the previous signature. `_check_and_reload_excel()` then assumes the file modification was an internal `ExcelSyncWorker` write and updates `self._last_excel_mtime = current_mtime`, silencing reload detection until the 5-minute (`300s`) patrol timer expires and `write_workbook_state()` re-saves the workbook without `read_only=True`.
2. `AuctionOrchestrator.run_patrol_step()` sequentially opens and inspects all eligible lots without calling `_check_and_reload_excel()` between lots, and captures `now = time.monotonic()` only once before the loop while indexing `self._last_patrolled_at` by `lot.lot_id`. When two rows in the Excel file point to the same Catawiki lot ID (for example with different `?poq=` search query parameters) or duplicate URLs, patrol timestamps and reconciliation mappings collide.

## Goals / Non-Goals

**Goals:**
- Guarantee `extract_input_signature()` reads all actual rows present in the worksheet regardless of XML `<dimension>` metadata.
- Detect and reconcile Excel changes between individual lot queries during an active `run_patrol_step()` pass, immediately inspecting newly added pending lots in the same pass.
- Prevent patrol starvation and signature drift when multiple rows reference the same Catawiki lot or URL.

**Non-Goals:**
- Changing the Excel template schema or column layout.
- Modifying Assault Mode bidding tactics or reserve price ladder rules.

## Decisions

### Decision 1: Use standard `load_workbook(target, data_only=True)` and `iter_rows` in `extract_input_signature()`
- **Choice**: Open the workbook with `load_workbook(target, data_only=True)` (without `read_only=True`) and iterate rows via `enumerate(ws.iter_rows(min_row=FIRST_DATA_ROW, max_col=2, values_only=True), start=FIRST_DATA_ROW)`.
- **Rationale**: Standard `openpyxl` worksheet loading parses all `<row>` elements directly from the XML stream and computes `max_row` from actual populated cells rather than trusting `<dimension ref="..."/>`. Because the control spreadsheet is small (~7–20 KB) and `extract_input_signature()` is only invoked when `st_mtime` changes on disk, standard loading takes ~2 ms while eliminating both the `<dimension>` truncation bug and the $O(N^2)$ XML re-parsing penalty of calling `ws.cell()` on a `ReadOnlyWorksheet`.
- **Alternatives considered**: Calling `ws.reset_dimensions()` on `ReadOnlyWorksheet` — rejected because `ws["B1"]` + `iter_rows` in `read_only=True` still parses the XML twice and offers no meaningful memory or speed advantage for a ~10 KB file only read on `mtime` changes.

### Decision 2: Check for Excel reloads between lots inside `run_patrol_step()`
- **Choice**: Inside `run_patrol_step()`, iterate using a dynamic index/visited set over `self.lots`, calling `await self._check_and_reload_excel()` before each lot inspection (after the first) and refreshing the current timestamp via `now = time.monotonic()` on each lot.
- **Rationale**: A patrol round over 10–15 lots can take 1–2+ minutes over CDP. Checking `_check_and_reload_excel()` between lots ensures that if the user saves new watches while a patrol pass is underway, the new rows are reconciled before `self._emit_and_enqueue()` flushes state and are inspected immediately during the same patrol pass.

### Decision 3: Per-row patrol tracking and duplicate URL/lot handling
- **Choice**:
  - Track patrol timestamps in `_last_patrolled_at` using a unique key per lot entry (or canonicalizing/deduplicating rows in `reconcile_with_excel` while preserving row-level uniqueness if URLs differ or are duplicated) and evaluate `time.monotonic()` per lot inside `run_patrol_step()`.
  - In `reconcile_with_excel()`, match existing lots by `(lot.url, lot.row_index)` with fallback to unused `lot.url` instances so that even if a user accidentally pastes duplicate URLs or two URLs for the same lot with different query strings, `self.lots` and `make_input_signature_from_memory()` always stay 1-to-1 with `new_config.lots`.
- **Rationale**: Prevents `make_input_signature_from_memory()` from diverging from `extract_input_signature()` when duplicate URLs exist, and prevents updating `_last_patrolled_at` on one row from starving another row in the same patrol round.

## Risks / Trade-offs

- **[Risk] Mutating `self.lots` mid-iteration in `run_patrol_step()` when `_check_and_reload_excel()` reconciles additions or removals** → **Mitigation**: Instead of a static `for lot in self.lots:` iterator, use a `while` loop that selects the next eligible lot not yet visited in the current patrol pass (or newly added in `PENDING` state), safely handling mid-round additions and removals.
