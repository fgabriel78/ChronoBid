# Proposal

## Why

After introducing dynamic Excel live-reloading (`v1.1.0`) and tiered 5-minute (`300s`) patrol intervals (`v1.1.1`), newly added rows saved to the Excel spreadsheet can remain undetected for over 3 to 5 minutes. Specifically, `extract_input_signature()` opens the workbook with `read_only=True` and relies on `ws.max_row` (which only reads the XML `<dimension>` attribute and ignores newly saved rows when the spreadsheet editor does not update `<dimension>`), causing `_check_and_reload_excel()` to mistake user edits for internal flushes and advance `_last_excel_mtime`; additionally, `run_patrol_step()` never checks for Excel updates between sequential lot inspections and skips rows that share a Catawiki `lot_id` with an earlier row.

## What Changes

- Replace `read_only=True` and `ws.max_row` indexing in `extract_input_signature()` with full actual-row iteration (`load_workbook(target, data_only=True)` + `ws.iter_rows`) so that newly added rows are always detected immediately after saving, regardless of XML `<dimension>` metadata.
- Check for live Excel reloads (`_check_and_reload_excel()`) between sequential lot queries inside `run_patrol_step()` so changes saved during a multi-minute patrol round are reconciled immediately without waiting for the entire round to finish.
- Evaluate `time.monotonic()` per lot inside `run_patrol_step()` and track patrol timestamps and reconciliation mappings consistently when multiple rows reference the same Catawiki lot ID (e.g. with different search query parameters) or duplicate URLs.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `excel-control`: Strengthen live input signature extraction and runtime reconciliation so newly added rows (including spreadsheets saved with stale or missing XML `<dimension>` tags or duplicate lot references) are detected within a single poll cycle and between individual patrol queries.
- `catawiki-browser-engine`: Ensure `run_patrol_step()` checks for live Excel reloads between sequential lot inspections and evaluates per-lot monotonic timestamps without starving lots that share a lot identifier.

## Impact

- `src/chronobid/excel/workbook_io.py`: `extract_input_signature()` implementation.
- `src/chronobid/orchestrator.py`: `_check_and_reload_excel()`, `reconcile_with_excel()`, and `run_patrol_step()`.
- `tests/test_workbook_io.py` and `tests/test_orchestrator_e2e.py`: Regression tests for stale/unsized XML `<dimension>` metadata, mid-patrol live reloads, and duplicate lot/URL rows.
