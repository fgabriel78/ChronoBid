# Tasks

## 1. OpenSpec Configuration

- [x] 1.1 Update `openspec/config.yaml` to include global context specifying that all future specifications, proposals, designs, tasks, docstrings, and comments must be in English.

## 2. Active Specifications Translation

- [x] 2.1 Translate `openspec/specs/bidding-strategy-portfolio/spec.md` to English and verify with `openspec validate --specs`.
- [x] 2.2 Translate `openspec/specs/catawiki-browser-engine/spec.md` to English and verify with `openspec validate --specs`.
- [x] 2.3 Translate `openspec/specs/excel-control/spec.md` to English and verify with `openspec validate --specs`.

## 3. Source Code Comments and Docstrings Translation

- [x] 3.1 Translate docstrings and inline comments in `src/chronobid/models/` and `src/chronobid/strategy/` to English while preserving runtime string constants.
- [x] 3.2 Translate docstrings and inline comments in `src/chronobid/browser/`, `src/chronobid/excel/`, `src/chronobid/orchestrator.py`, and `src/chronobid/cli.py` to English.

## 4. Test Suite Translation and Regression Verification

- [x] 4.1 Translate docstrings and inline comments across `tests/` into English while preserving all test assertions.
- [x] 4.2 Run `pytest tests/ -v` and `openspec validate --specs` to verify 100% test pass and schema validity.
