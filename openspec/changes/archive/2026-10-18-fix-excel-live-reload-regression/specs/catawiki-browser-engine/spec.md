# catawiki-browser-engine Spec Delta

## MODIFIED Requirements

### Requirement: Differentiated operation in Patrol Mode and Assault Mode
The system SHALL monitor watches with far closing times (> 5 minutes) using an adaptive tiered cadence based on each individual lot's remaining time (Patrol Mode) and switch to continuous real-time tracking (Assault Mode) when less than 5 minutes remain before closing for a viable watch, ensuring strict cadence independence, live Excel reload responsiveness between sequential patrol queries, and resource usage minimization.

#### Scenario: Transition from Patrol Mode to Assault Mode
- **WHEN** a viable watch enters the last 5 minutes prior to its closing time
- **THEN** the system opens or activates dedicated real-time tracking for that lot with sub-second timer polling, current bid, leadership status, and reserve price alert

#### Scenario: Tiered cadence by time windows in Patrol Mode
- **WHEN** an active lot in Patrol Mode has a known remaining time greater than 5 minutes
- **THEN** the system applies an individualized review cadence: every 300 seconds if more than 3,600 seconds remain (> 1 hour), every 120 seconds if between 900 and 3,600 seconds remain (15 to 60 minutes), and every 60 seconds if between 300 and 900 seconds remain (5 to 15 minutes), evaluating elapsed time against the current monotonic clock per lot and avoiding lot inspection until its corresponding interval has elapsed

#### Scenario: Immediate priority for pending lots without prior query
- **WHEN** a new lot is added to the catalog or a lot remains in PENDING status without a prior snapshot, including when added to Excel while a patrol round is already in progress
- **THEN** the system detects the Excel modification between sequential patrol queries and immediately inspects the new pending lot without waiting for the current patrol round or a full patrol interval to finish

#### Scenario: Adaptive anticipation of Assault Mode threshold
- **WHEN** a viable watch in the upcoming window (5 to 15 minutes) has remaining time such that the next periodic poll would fall below 300 seconds
- **THEN** the system dynamically adjusts the patrol loop sleep to wake up and inspect the lot at the exact second it reaches 300 seconds remaining, transitioning to Assault Mode without delay

#### Scenario: Accurate extraction of current bid and auction status
- **WHEN** the system inspects a lot in Patrol Mode or Assault Mode
- **THEN** the system extracts the exact current bid amount, calculates the next minimum bid step according to Catawiki's official increment tiers (including 1-10 € micro-bids at +1 €), and classifies reserve status (MET, NOT_MET, or NO_RESERVE) combining injected SSR state with lot DOM selectors

#### Scenario: Handling opening lots without prior bids
- **WHEN** an active lot under inspection has no prior bids in its history or displays initial opening state
- **THEN** the system adopts the starting bid or first quick-bid button as the next acceptable minimum step without discarding the lot through strict comparison against a fictitious current bid, and as soon as live DOM or network reflects a registered bid or user leadership in the same tab, stops forcing current bid to zero even if the initial SSR state remains unrefreshed

#### Scenario: Transient failure tolerance during patrol round
- **WHEN** inspecting a lot during patrol returns a temporary network or platform error
- **THEN** the orchestrator maintains the lot in its active state, does not propagate empty or closed values to the model, and retries the read in the next patrol round

#### Scenario: Cadence independence in Patrol Mode during concurrent Assault Mode
- **WHEN** one or more viable watches are in continuous Assault Mode tracking and other viable watches have closing times greater than 5 minutes
- **THEN** the system maintains Patrol Mode monitoring with queries spaced according to each lot's tiered patrol interval, without accelerating polls or continuously reloading patrol tabs due to concurrent assaults

#### Scenario: Suspension of patrol polls when all active lots are in assault
- **WHEN** all active viable watches are already in Assault Mode with dedicated tracking
- **THEN** the system suspends patrol rounds and passively awaits the resolution of assault tasks without high-frequency busy waits or redundant market polls
