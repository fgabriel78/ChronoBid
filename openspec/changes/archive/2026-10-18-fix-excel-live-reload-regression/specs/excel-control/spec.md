# excel-control Spec Delta

## MODIFIED Requirements

### Requirement: Reading global configuration and watch catalog
The system SHALL read from the Excel spreadsheet the global maximum budget in euros and the list of candidate watches consisting of their Catawiki URL and user-authorized clean maximum bid, detecting live modifications across all actual worksheet rows regardless of XML dimension metadata and reconciling active in-memory state during execution.

#### Scenario: Valid initial loading of Excel file
- **WHEN** the agent starts or reloads the Excel file with a positive global budget and rows with URL and clean maximum bid
- **THEN** the system registers the global budget and creates an active tracking entry for each valid watch

#### Scenario: Row with incomplete or invalid data
- **WHEN** a row in the Excel file lacks a Catawiki URL or has a non-numeric or non-positive maximum bid
- **THEN** the system marks the status of that row as a validation error and continues processing remaining watches

#### Scenario: Dynamic reconciliation on live global budget modification
- **WHEN** the user modifies the Global Budget cell (B1) in the Excel file and saves changes during execution
- **THEN** the system detects the modification, updates the global budget in the atomic registry (Ledger), and recalculates available balance and dynamic portfolio ceilings without restarting the orchestrator

#### Scenario: Dynamic addition of new watches at runtime
- **WHEN** the user adds a new row with a Catawiki URL and valid maximum bid in the Excel file during execution, even if the spreadsheet editor saves the file with stale or unsized XML `<dimension>` metadata
- **THEN** the system scans all actual worksheet rows, adds the new lot to the active portfolio in pending status within the next poll interval (or before the next lot query if a patrol round is currently running), and triggers an immediate patrol query to extract its market data

#### Scenario: Dynamic maximum bid modification and reactivation of viable lots
- **WHEN** the user modifies the maximum bid of an existing watch in the Excel file
- **THEN** the system updates the watch's individual ceiling, and if the watch was discarded for exceeding its prior ceiling and the new maximum bid is greater than or equal to the minimum required bid on Catawiki, reactivates the watch to active state (Patrol Mode or Assault Mode depending on its closing time)

#### Scenario: Deletion of watches from Excel during execution
- **WHEN** the user deletes a row or empties the URL of a watch in the Excel file
- **THEN** the system cancels tracking for the watch, orderly halts any dedicated assault task for that lot, releases its committed budget in the Ledger if not awarded, and removes it from the active portfolio

#### Scenario: Tolerance to read locks from concurrent editing in Excel
- **WHEN** the orchestrator attempts to read the Excel file to verify changes while the user keeps it open and locked for saving
- **THEN** the system tolerates the transient file sharing error on Windows (PermissionError/OSError), keeping the in-memory state intact and retrying the read in the next check
