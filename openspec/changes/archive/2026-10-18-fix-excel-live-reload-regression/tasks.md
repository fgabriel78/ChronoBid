# Tasks

## 1. Resilient Excel Input Signature Extraction

- [x] 1.1 Update `extract_input_signature()` in `src/chronobid/excel/workbook_io.py` to open the workbook with `load_workbook(target, data_only=True)` (without `read_only=True`) and iterate rows via `ws.iter_rows(min_row=FIRST_DATA_ROW, max_col=2, values_only=True)` so all actual rows are detected even when the XML `<dimension>` tag is stale or unsized, and verify existing unit tests in `tests/test_workbook_io.py` pass
- [x] 1.2 Add a regression test in `tests/test_workbook_io.py` that simulates an `.xlsx` file saved with a stale/truncated `<dimension ref="A1:J7"/>` XML attribute while containing additional rows (e.g. rows 8–10) and verify `extract_input_signature()` extracts all rows

## 2. Mid-Patrol Live Reload and Per-Row Patrol Tracking

- [x] 2.1 Update `reconcile_with_excel()` and `_last_patrolled_at` tracking in `src/chronobid/orchestrator.py` so duplicate URLs or rows sharing the same Catawiki `lot_id` (with different query parameters or rows) maintain 1-to-1 signature parity and independent patrol timestamps, and verify `run_patrol_step()` evaluates `time.monotonic()` per lot and checks `_check_and_reload_excel()` between sequential lot inspections
- [x] 2.2 Add regression tests in `tests/test_orchestrator_e2e.py` covering (a) live addition of new watches in Excel while `run_patrol_step()` is actively inspecting existing lots, and (b) multiple rows with the same `lot_id` / duplicate URLs being patrolled without starving the second row, and verify `pytest -v` passes
