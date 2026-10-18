## MODIFIED Requirements

### Requirement: Prior invisibility and minimum increment bidding execution
The system SHALL refrain from placing bids until the configured final seconds before closing (< 25 seconds) and, when bidding, MUST submit only the minimum bid step required by Catawiki without revealing the user's maximum price to the platform, automatically resolving any "Press & Hold" human verification modal that intercepts the bid submission or confirmation flow.

#### Scenario: First entry in final seconds
- **WHEN** less than 25 seconds remain before closing, the user is not the current highest bidder, and the next minimum bid step does not exceed the assigned dynamic ceiling
- **THEN** the system executes a bid for the exact amount of the next valid minimum step

#### Scenario: Bid submission via quick button or numeric field
- **WHEN** submitting the minimum bid step to the lot page is triggered
- **THEN** the system clicks the quick-bid button if it matches the exact amount or inputs the value into the bid field and clicks the direct bid button (never the automatic maximum bid button), actively waits for the Catawiki confirmation modal dialog to mount, clicks the primary confirm bid button, and awaits status update before extracting a new snapshot

#### Scenario: Automatic resolution of Press & Hold challenge during bid submission
- **WHEN** a "Press & Hold" verification challenge modal (`#px-captcha`, challenge iframe, or "Press & Hold" button) is present or appears while submitting or confirming a bid
- **THEN** the system locates the target button's bounding box across the main document and child frames, moves the pointer to the target coordinates, presses and holds the mouse button continuously until the challenge modal disappears from the DOM (or the hold timeout expires), releases the mouse button, and awaits Catawiki's automatic bid registration settlement before extracting the post-bid snapshot

#### Scenario: Registration conditioned on effective bid acceptance
- **WHEN** the attempt to submit and confirm a bid in Assault Mode finishes and the post-bid lot snapshot is obtained
- **THEN** the orchestrator registers its own bid only if the snapshot confirms that the user is leader or that the market current bid has reached at least the submitted amount; otherwise, it immediately releases the reserved budget and does not falsely mark the lot as outbid
