# operational-diagnostics Specification

## Purpose

Provides persistent, per-session diagnostic records of monitoring, tactical decisions, bid attempts and browser incidents so that the outcome of every lot can be reconstructed after the fact.

## Requirements

### Requirement: Persistent per-session diagnostic log
The system SHALL write a diagnostic log file for every agent session to a `logs` directory located next to the controlling Excel file, using a timestamped file name, without interfering with the live dashboard output.

#### Scenario: Log file created at session start
- **WHEN** the agent starts monitoring an Excel file
- **THEN** the system creates the `logs` directory if missing, opens a new log file whose name contains the session start date and time, and records the session start, Excel path, global budget and loaded lots

#### Scenario: Dashboard is not polluted by log output
- **WHEN** diagnostic events are logged while the live dashboard is displayed
- **THEN** the events are written to the log file and do not corrupt or interleave with the dashboard rendering in the terminal

#### Scenario: Log file not writable
- **WHEN** the log directory or file cannot be created or written
- **THEN** the system notifies the user once in the terminal and continues monitoring and bidding without diagnostic logging

### Requirement: Tactical decision trace in Assault Mode
The system SHALL record, for each lot in Assault Mode, every tactical decision whose outcome or reason changes, and every evaluation tick once the lot is inside the snipe or overtime counter window, including remaining seconds, current bid, next minimum bid, leadership, dynamic ceiling, available budget, decision and reason.

#### Scenario: Assault entry and exit recorded
- **WHEN** a lot enters or leaves Assault Mode
- **THEN** the log records the transition with the lot identifier, row, remaining seconds and reason

#### Scenario: Ticks inside the snipe window recorded
- **WHEN** a lot in Assault Mode is evaluated with remaining time inside the snipe window or the overtime counter window
- **THEN** the log records one entry per evaluation with the full decision context, so the absence of a bid can be explained

#### Scenario: Repeated identical decisions outside the snipe window
- **WHEN** a lot in Assault Mode is evaluated outside the snipe window and the decision and reason are identical to the previous evaluation
- **THEN** the system does not write a new entry for that evaluation

### Requirement: Bid attempt and incident trace
The system SHALL record every bid attempt with its amount, start and end times, whether a human verification challenge was encountered, the confirmation outcome and the post-bid market state, and SHALL record every page-load failure, verification challenge and unexpected error with the affected lot.

#### Scenario: Successful or unconfirmed bid recorded
- **WHEN** the agent submits a bid on a lot
- **THEN** the log records the bid amount, the elapsed submission time, whether a challenge was solved, whether the bid was confirmed by the post-bid snapshot, and the post-bid current bid and leadership

#### Scenario: Incident recorded
- **WHEN** reading or bidding on a lot raises a page-load error, a human verification challenge or an unexpected exception
- **THEN** the log records the lot identifier, the remaining seconds if known, the incident type and its message

#### Scenario: Final outcome recorded
- **WHEN** a lot reaches a terminal status
- **THEN** the log records the final status, closing price, own last bid and the last meaningful event that preceded closure
