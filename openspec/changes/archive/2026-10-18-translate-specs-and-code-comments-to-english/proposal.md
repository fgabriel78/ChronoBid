# Proposal

## Why

All existing active specifications in OpenSpec and source code docstrings/comments across ChronoBid were originally written in Spanish. To standardize the project for international collaboration and align with industry standards, all active specifications and source code comments/docstrings must be translated to English, and the OpenSpec configuration must enforce English as the default language for all future artifacts.

## What Changes

- **OpenSpec Default Language Configuration**: Update `openspec/config.yaml` with a project-level `context` constraint enforcing English for all future specifications, proposals, designs, tasks, docstrings, and source code comments.
- **Active Specifications Translation**: Translate all active capability specifications under `openspec/specs/` (`bidding-strategy-portfolio`, `catawiki-browser-engine`, and `excel-control`) from Spanish to idiomatic English, preserving all behavioral requirements and scenarios.
- **Source Code Docstrings and Comments**: Translate all module-level, class-level, and method-level docstrings as well as inline comments across `src/chronobid/` to clear English.
- **Test Suite Docstrings and Comments**: Translate docstrings and comments across `tests/` to English.
- **Preserved Boundaries (Non-Goals)**:
  - Runtime domain strings displayed in Excel and UI (e.g. `AuctionStatus` and `ReserveStatus` values such as `"PENDIENTE"`, `"GANADO"`, `"ALCANZADA"`) remain unchanged to maintain full compatibility with user spreadsheets.
  - Logger messages and CLI argument help text remain in their current form.
  - Historical archive folders under `openspec/changes/archive/` remain untouched as immutable records.

## Capabilities

### New Capabilities
*(None; this change standardizes language and documentation across existing capabilities).*

### Modified Capabilities
*(None; this change is a documentation, configuration, and comment translation refactor that updates existing specs and code directly, using `skip_specs: true`).*

## Impact

- `openspec/config.yaml`: Sets English as the persistent default context for all future OpenSpec tasks.
- `openspec/specs/**/*.md`: Active specifications updated to English.
- `src/chronobid/**/*.py`: Docstrings and comments translated to English without altering any runtime logic or string constants.
- `tests/**/*.py`: Test docstrings and comments translated to English.
