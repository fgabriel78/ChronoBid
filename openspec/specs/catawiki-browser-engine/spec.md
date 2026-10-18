# catawiki-browser-engine Specification

## Purpose

Provides the interaction interface with Catawiki over an authenticated browser session to extract real-time watch auction state and execute tactical bids in the final seconds and overtime extensions.

## Requirements

### Requirement: Connection to authenticated browser session
The system SHALL connect to an existing browser instance already opened and authenticated by the user on Catawiki, reusing session credentials, cookies, and browser fingerprint without prompting for passwords or requiring automated login.

#### Scenario: Successful connection to user browser
- **WHEN** the agent starts and the user browser is available with an active session on Catawiki
- **THEN** the system attaches to the browser and verifies that the user session is authenticated before operating

#### Scenario: Browser unavailable or session not logged in
- **WHEN** the agent cannot attach to the browser or detects that the user is not logged into Catawiki
- **THEN** the system clearly notifies the connection/authentication requirement and halts bid submission until restored

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

### Requirement: Resilience against transient loading errors on Catawiki
The system SHALL detect Catawiki transient error pages, platform failure responses, or missing auction containers, cataloging them as temporary issues without overwriting the lot title or forcing the countdown timer to zero.

#### Scenario: Detection of We can't load this page right now error screen
- **WHEN** the system navigates to a lot URL and the page displays error headers or text such as "We can't load this page right now", HTTP 5xx errors, or pages without auction containers
- **THEN** the system rejects the extraction as an invalid market snapshot, preserves the lot's prior title and state, and avoids transitioning the auction to closed or lost

#### Scenario: Preservation of missing countdown on active lot
- **WHEN** an open auction page loads but countdown selectors do not return a numerical value and there is no explicit closed lot text
- **THEN** the system records remaining time as null (`None`) instead of forcing it to `0.0`, preventing the auction from being interpreted as concluded due to time expiration

### Requirement: Prior invisibility and minimum increment bidding execution
The system SHALL refrain from placing bids until the configured final seconds before closing (< 25 seconds) and, when bidding, MUST submit only the minimum bid step required by Catawiki without revealing the user's maximum price to the platform.

#### Scenario: First entry in final seconds
- **WHEN** less than 25 seconds remain before closing, the user is not the current highest bidder, and the next minimum bid step does not exceed the assigned dynamic ceiling
- **THEN** the system executes a bid for the exact amount of the next valid minimum step

#### Scenario: Bid submission via quick button or numeric field
- **WHEN** submitting the minimum bid step to the lot page is triggered
- **THEN** the system clicks the quick-bid button if it matches the exact amount or inputs the value into the bid field and clicks the direct bid button (never the automatic maximum bid button), actively waits for the Catawiki confirmation modal dialog to mount, clicks the primary confirm bid button, and awaits status update before extracting a new snapshot

#### Scenario: Registration conditioned on effective bid acceptance
- **WHEN** the attempt to submit and confirm a bid in Assault Mode finishes and the post-bid lot snapshot is obtained
- **THEN** the orchestrator registers its own bid only if the snapshot confirms that the user is leader or that the market current bid has reached at least the submitted amount; otherwise, it immediately releases the reserved budget and does not falsely mark the lot as outbid

### Requirement: Management of +90 second extension loop
The system SHALL continue monitoring the lot following any time extension (+90 seconds) caused by bids in the final minute and counter-bid when another bidder outbids the user, provided the new minimum step does not exceed the dynamic ceiling or available budget.

#### Scenario: Counter-attack during 90-second extension
- **WHEN** a competitor outbids the user during the extension window and the next minimum increment is within the active dynamic ceiling
- **THEN** the system submits the required minimum counter-bid and updates the new closing countdown

#### Scenario: Abandonment when rival exceeds permitted ceiling
- **WHEN** the bid required to surpass the current leader exceeds the dynamic ceiling or the watch's maximum bid
- **THEN** the system stops bidding on that lot and immediately releases any budget previously committed to it

### Requirement: Tiered reserve price unlocking in final seconds
The system SHALL increase its own bid step by step in the final seconds when the user is already the highest bidder but the auction indicates the reserve price has not been met, stopping immediately as soon as the reserve alert disappears or the permitted ceiling is reached.

#### Scenario: Reserve unlocked before exhausting ceiling
- **WHEN** the user is leader in the final 25 seconds with a "reserve price not met" notice and the system raises a minimum step after which the notice disappears
- **THEN** the system immediately stops additional self-bids and holds the winning position at minimum unlocking cost

#### Scenario: Reserve above permitted maximum
- **WHEN** the system raises steps attempting to reach reserve and the next increment exceeds the watch's maximum bid or available dynamic ceiling
- **THEN** the system stops self-bidding without ever exceeding the established limit
