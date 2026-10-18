# excel-control Specification

## Purpose

Manages reading input parameters (global budget in euros, watch URLs, and clean maximum bids) and asynchronously updating auction status in an Excel file in the background without blocking bidding operations.

## Requirements

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

### Requirement: Reporting auction status and metrics per watch
The system SHALL write and keep updated in the Excel file the control columns for each watch: lot title, closing time, auction status, own last bid, current or winning bid, reserve price status, and assigned dynamic ceiling.

#### Scenario: Status update after placing a bid
- **WHEN** the agent executes a valid bid on Catawiki for a watch in the list
- **THEN** the system updates the corresponding row in Excel reflecting the amount in "Mi Última Puja", the lot's current bid, and "LIDER" status or applicable status

#### Scenario: Recording final auction outcome
- **WHEN** an auction concludes definitively after exhausting its time and extensions
- **THEN** the system writes the final status to Excel ("GANADO", "PERDIDO: Superó Máximo", "PERDIDO: Sin Presupuesto", or "NO ADJUDICADO: Reserva No Alcanzada") along with the closing price

#### Scenario: Preservation of title and active status on transient query failure
- **WHEN** querying Catawiki for an active lot fails temporarily due to service degradation or network error
- **THEN** the system preserves in Excel the legitimate title previously recorded and the active status of the watch, noting the retry event in "Notas / Último Evento" without marking the auction as "PERDIDO" or closing the lot

### Requirement: Reporting global budget accounting in Excel
The system SHALL update in the header of the Excel file the global budget breakdown: firmly spent amount (won watches), committed in-flight amount (active watches where the user is leader), and available balance.

#### Scenario: Reflection of committed and available budget
- **WHEN** the user becomes the provisional highest bidder in an active auction or wins a lot
- **THEN** the system recalculates and updates the spent, committed, and available budget cells in Excel

### Requirement: Lock-tolerant background writing
The system SHALL queue pending updates to the Excel file in memory when the file is locked by another process in the operating system, without delaying or interrupting active monitoring or ongoing bidding.

#### Scenario: User opens Excel during an active auction
- **WHEN** the agent attempts to save a status change to the Excel file while the user has it open and locked for writing
- **THEN** the system retains the updated state in memory, continues executing bids normally, and automatically persists pending changes as soon as the file becomes unlocked
