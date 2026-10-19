# Spec Delta

## ADDED Requirements

### Requirement: Human verification resolution during navigation and market reads
The system SHALL detect a "Press & Hold" human verification challenge after navigating to a lot and before every market snapshot extraction, and SHALL attempt to resolve it automatically before reading auction data, so that a challenge never silently prevents monitoring or bidding.

#### Scenario: Challenge shown when opening the assault tab
- **WHEN** the dedicated Assault Mode tab navigates to a lot and Catawiki displays a full-page or modal "Press & Hold" challenge
- **THEN** the system resolves the challenge using the same press-and-hold procedure used during bid submission and then extracts the market snapshot from the restored lot page

#### Scenario: Challenge appears during continuous tracking
- **WHEN** a "Press & Hold" challenge appears on a lot tab between two market reads in Patrol Mode or Assault Mode
- **THEN** the system resolves it before the next snapshot extraction instead of treating the page as a transient load error

#### Scenario: Challenge cannot be resolved
- **WHEN** the challenge is still present after the hold timeout expires
- **THEN** the system reports the read as blocked by human verification (distinct from a transient load error), keeps the lot active with its previous market state, and retries resolution on the next tick

### Requirement: Verification-blocked lot status
The system SHALL expose a non-terminal status "BLOQUEADO: Verificación Humana" for any active lot whose latest read or bid attempt was blocked by an unresolved human verification challenge, and SHALL restore the regular status as soon as a valid snapshot is obtained.

#### Scenario: Lot blocked by verification
- **WHEN** a lot read or bid attempt ends blocked by an unresolved human verification challenge
- **THEN** the dashboard and Excel show the lot as "BLOQUEADO: Verificación Humana", its notes state the remaining seconds when the block was detected, and the lot remains eligible for monitoring and bidding

#### Scenario: Block cleared
- **WHEN** a subsequent read of a verification-blocked lot returns a valid market snapshot
- **THEN** the system replaces the blocked status with the status derived from the new snapshot (Patrol, Assault, Leader, Outbid or a terminal status)

### Requirement: Proactive challenge clearance before the snipe window
The system SHALL check the assault tab for a pending human verification challenge, and resolve it, before the lot enters the snipe window, so that verification latency does not consume the time reserved for bidding.

#### Scenario: Pre-snipe check
- **WHEN** a lot in Assault Mode reaches the configured pre-snipe check point (a few seconds before the snipe window opens)
- **THEN** the system verifies that no challenge is pending on the tab and resolves any challenge found before the snipe window opens

### Requirement: Bid retry within the snipe window
The system SHALL re-evaluate and, if still due, resubmit the next minimum bid on the following tick after a bid attempt that failed or was not confirmed, as long as the lot remains open, the user is not leader, and the new minimum step fits within the dynamic ceiling and available budget.

#### Scenario: Unconfirmed bid retried
- **WHEN** a bid attempt in the snipe window is not confirmed by the post-bid snapshot and time remains before closing
- **THEN** the system releases the reservation, re-reads the market on the next tick and submits the current next minimum step if it is still within the ceiling and budget

#### Scenario: Bid submission error retried
- **WHEN** a bid attempt raises an error (missing bid controls, verification challenge or page failure) and time remains before closing
- **THEN** the system records the error, releases the reservation and retries on the next tick under the same conditions

### Requirement: Reliable leadership detection
The system SHALL determine whether the user is the provisional highest bidder only from user-specific leadership indicators (notices addressed to the user such as "Eres el máximo pujador", injected lot state or bidding network payloads), and MUST NOT infer leadership from generic phrases or from negative notices that merely contain a leadership phrase.

#### Scenario: Generic text mentioning highest bidder
- **WHEN** the lot page contains generic text such as "highest bidder" that is not addressed to the user (e.g. help text, bid history)
- **THEN** the system does not mark the user as leader

#### Scenario: Negative leadership notice
- **WHEN** the lot page shows a notice such as "Ya no eres el máximo pujador" / "You're no longer the highest bidder"
- **THEN** the system does not mark the user as leader

#### Scenario: User-specific leadership indicator present
- **WHEN** the lot bid status indicator states that the user holds the highest bid (e.g. "Eres el máximo pujador" / "You're the highest bidder") or the lot state reports the user as leader
- **THEN** the system marks the user as provisional leader
