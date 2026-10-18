# bidding-strategy-portfolio Specification

## Purpose

Governs the atomic accounting of the global budget in euros and the dynamic portfolio optimization strategy to maximize the total number of watches won without ever exceeding individual limits or the global budget.

## Requirements

### Requirement: Atomic in-flight budget accounting (Ledger)
The system SHALL maintain real-time atomic accounting where available budget equals the global budget minus the sum of already won watches and minus the sum of active bids where the user is currently the provisional highest bidder.

#### Scenario: Balance locking upon becoming provisional highest bidder
- **WHEN** the agent places a bid and becomes the provisional leader of a watch for an amount $X$
- **THEN** the system atomically deducts $X$ (or the differential increase if already leader for a smaller amount) from the available budget before allowing bids on other concurrent watches

#### Scenario: Committed balance synchronization upon detecting leadership in market read
- **WHEN** the system inspects an active lot in Patrol Mode or Assault Mode and detects that the user is the provisional highest bidder for an amount $X > 0$ (including previous manual bids on Catawiki or restarted sessions)
- **THEN** the system atomically updates the committed amount as leader for that watch to $X$ and deducts this commitment from the available budget

#### Scenario: Immediate balance release upon being outbid by another bidder
- **WHEN** another user outbids the user in an active auction, both during Patrol Mode and during Assault Mode
- **THEN** the system instantly releases the amount previously committed to that watch and restores it to the available budget

### Requirement: Strict individual ceiling and global budget invariant
The system MUST reject any bid attempt whose amount exceeds the clean maximum bid defined in Excel for that watch or whose additional disbursement exceeds the available global budget at that instant.

#### Scenario: Bid attempt that would exceed available budget due to overlapping auctions
- **WHEN** two watches are closing simultaneously and the required bid on the second watch exceeds the available budget after deducting the active winning bid on the first watch
- **THEN** the system blocks the bid on the second watch to guarantee that the total sum of commitments never exceeds the global budget

### Requirement: Automatic pruning of overpriced watches
The system SHALL automatically discard from the active portfolio any watch whose next minimum required bid on Catawiki exceeds the clean maximum bid specified by the user in Excel.

#### Scenario: Watch exceeds maximum before entering final minute
- **WHEN** during monitoring it is detected that the next minimum bid increment of a watch is greater than its maximum bid in Excel
- **THEN** the system marks the watch as discarded for exceeding maximum and excludes it from the budget reservation calculation for future lots

### Requirement: Dynamic ceiling calculation to maximize number of watches
The system SHALL dynamically calculate an effective ceiling ($E_i \le M_i$) for each active auction by evaluating the current cost and price headroom of remaining viable pending watches, reserving budget when a combination of highly viable future watches allows acquiring a greater number of units with the same available balance.

#### Scenario: Budget reservation for two cheap future watches with high headroom
- **WHEN** an active watch requires consuming an amount that would leave insufficient balance for a combination of two or more viable future watches that have ample margin under their respective maximums
- **THEN** the system reduces the effective dynamic ceiling of the active watch to preserve the balance required for the combination that maximizes the number of watches

#### Scenario: Full ceiling release when future watches lose viability
- **WHEN** remaining pending future watches rise in price and reduce their headroom margin or are discarded
- **THEN** the system automatically raises the dynamic ceiling of the active watch up to the maximum permitted by its individual ceiling ($M_i$) and the available budget
