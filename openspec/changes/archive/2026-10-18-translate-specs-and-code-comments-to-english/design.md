# Design

## Context

See `proposal.md` for motivation and scope boundaries. ChronoBid is currently documented and annotated in Spanish, both in its active OpenSpec specifications (`openspec/specs/`) and across its Python codebase (`src/chronobid/` and `tests/`).

## Goals / Non-Goals

**Goals:**
- Enforce English as the mandatory default language for all future OpenSpec artifacts and codebase additions by adding a persistent `context` constraint to `openspec/config.yaml`.
- Translate all 3 active main specifications in `openspec/specs/` (`bidding-strategy-portfolio`, `catawiki-browser-engine`, and `excel-control`) into precise, idiomatic English.
- Translate all module-level, class-level, and function/method-level docstrings as well as inline comments in `src/chronobid/` and `tests/` to English.
- Verify that 100% of the test suite (`pytest tests/ -v`) passes without regressions and `openspec validate --specs` remains fully valid.

**Non-Goals:**
- Do not modify runtime domain string constants (`AuctionStatus` and `ReserveStatus` values such as `"PENDIENTE"`, `"LIDER"`, `"GANADO"`, `"ALCANZADA"`) to ensure backward compatibility with user Excel files.
- Do not translate logger output strings or CLI argument help text.
- Do not modify historical change archives under `openspec/changes/archive/`.

## Decisions

### Decision 1: Persistent language rule in `openspec/config.yaml`
- Update `openspec/config.yaml` to define a global `context` property:
  ```yaml
  context: |
    All specifications, proposals, designs, tasks, and documentation MUST be written in English.
    All source code comments, docstrings, and tests MUST be written in English.
  ```
- **Rationale**: OpenSpec injects this context constraint into all instructions for new changes and artifact generation, guaranteeing that future changes adhere to English by default without relying on manual prompt reminders.

### Decision 2: In-place translation of active specifications
- Directly translate the 3 active specifications under `openspec/specs/`:
  - `openspec/specs/bidding-strategy-portfolio/spec.md`
  - `openspec/specs/catawiki-browser-engine/spec.md`
  - `openspec/specs/excel-control/spec.md`
- **Rationale**: Since this is a pure language standardization refactor that preserves all underlying behavioral requirements and scenarios, updating the main specs in-place with `skip_specs: true` declared in the change avoids artificial scenario title mismatches in delta spec validation.

### Decision 3: Translation of source code docstrings and comments
- Review every `.py` file under `src/chronobid/`:
  - Module headers and docstrings (`"""..."""`)
  - Class and method docstrings
  - Inline explanations (`# ...`)
- **Rationale**: Keeps technical intent clear and maintainable for English-speaking developers while leaving executable statements completely untouched.

### Decision 4: Translation of test suite documentation
- Translate module docstrings, function docstrings, and explanatory comments across `tests/test_*.py` into English, leaving all assertion values untouched.

## Risks / Trade-offs

- **Risk**: Inadvertently modifying a runtime string literal instead of a docstring or comment (e.g. changing an error phrase or an Excel column value).
  - *Mitigation*: Strictly inspect each change to ensure only `#` comments and `"""` docstrings are translated. Verify immediately with `pytest tests/ -v`.
