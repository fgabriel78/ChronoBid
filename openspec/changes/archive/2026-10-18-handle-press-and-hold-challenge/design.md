# Design

## Context

See `proposal.md` for motivation. `CatawikiLotPage.submit_minimum_bid` currently interacts with Catawiki using Playwright locators over an authenticated CDP connection:
1. Dismisses any stale confirmation modal.
2. Clicks a matching quick-bid button or fills the numeric bid input and clicks submit.
3. Polls up to `confirm_appear_timeout_seconds` (2.5s) for `LotBidConfirmation` and clicks the primary confirm button.
4. Sleeps `confirmation_wait_seconds` (0.45s) and returns `await self.extract_snapshot()`.

When Catawiki's PerimeterX / HUMAN Security integration intercepts the bid action, a modal with a "Press & Hold" button (`#px-captcha` container, often hosting an internal `iframe` or `[role="button"]`) mounts over the page. Once the user holds the pointer down on the button continuously until the verification completes, the modal closes and Catawiki automatically completes the held bid submission without requiring a second click on the bid button.

## Goals / Non-Goals

**Goals:**
- Detect the "Press & Hold" challenge reliably across both the main page DOM and any child `page.frames`.
- Resolve the challenge automatically via continuous pointer press-and-hold (`page.mouse.move`, `page.mouse.down`, polling until dismissal, `page.mouse.up`) over the button's bounding box.
- Handle challenge appearance at any point in `submit_minimum_bid`: pre-existing before bidding, appearing before/instead of `LotBidConfirmation`, or appearing immediately after clicking `Confirmar puja`.
- Preserve fast execution when no challenge appears.

**Non-Goals:**
- Third-party external CAPTCHA solving services or API tokens.
- Modifying `BiddingTactics` or `BudgetLedger` rules.
- Re-submitting a duplicate bid after the challenge resolves when Catawiki automatically registers the held bid.

## Decisions

### 1. Multi-context target detection and bounding box resolution
- **Choice**: Define `press_and_hold_selectors` in `CatawikiSelectors` ordered from most specific button/widget container to fallback selectors:
  - `#px-captcha iframe`
  - `#px-captcha`
  - `[id*="px-captcha"]`
  - `button:has-text("Press & Hold")`
  - `[role="button"]:has-text("Press & Hold")`
  - `button:has-text("Mantén pulsado")`
  - `[role="button"]:has-text("Mantén pulsado")`
  - `div:has-text("Press & Hold to confirm")` (with vertical offset toward the button area only if inner selectors are absent)
- **Rationale**: In Catawiki's PerimeterX modal, `#px-captcha` and its inner `iframe` wrap the pill-shaped "Press & Hold" button directly, whereas the outer white dialog contains the Catawiki logo, text, and Reference ID footer. Prioritizing `#px-captcha iframe`, `#px-captcha`, and explicit button selectors (checked in both `self.page` and `getattr(self.page, "frames", [])`) ensures the center of `bounding_box()` lands directly inside the interactive button pill.
- **Alternatives considered**: Clicking via `locator.click(delay=...)`. Rejected because cross-origin iframes inside `#px-captcha` may block internal DOM event synthesis, whereas `page.mouse` dispatches trusted CDP input events at the viewport coordinates returned by `bounding_box()`.

### 2. Continuous hold lifecycle with guaranteed pointer release
- **Choice**: Implement `_solve_press_and_hold(self) -> bool` on `CatawikiLotPage` with configurable parameters:
  - `press_and_hold_max_seconds: float = 8.0`
  - `press_and_hold_poll_seconds: float = 0.1`
  - `post_challenge_wait_seconds: float = 0.8`
  When a challenge locator is visible:
  1. Resolve its `bounding_box()` (or fallback to element `.click(delay=...)` in simplified test doubles without `bounding_box`/`mouse`).
  2. Compute target coordinates `(x + width / 2 + jitter_x, y + height / 2 + jitter_y)` with small deterministic/bounded offset within the central 30% of the box.
  3. Call `await self.page.mouse.move(target_x, target_y, steps=8)` followed by `await self.page.mouse.down()`.
  4. In a `try ... finally` block, poll every `press_and_hold_poll_seconds` while the challenge remains present in the DOM, up to `press_and_hold_max_seconds`. Once the challenge disappears (or after timeout), always execute `await self.page.mouse.up()`.
- **Rationale**: Wrapping the hold loop in `try ... finally` guarantees the mouse button is never left depressed if an exception or task cancellation occurs. Stopping the hold as soon as the challenge element detaches from the DOM minimizes latency once verification succeeds (~3–5s in practice).

### 3. Placement in `submit_minimum_bid` flow
- **Choice**:
  1. **Pre-bid check**: Before clicking quick-bid or filling the input, check and resolve any pre-existing "Press & Hold" modal.
  2. **Confirmation loop**: While polling for `bid_confirm_selectors` (`LotBidConfirmation`), also check if a "Press & Hold" modal mounted instead. If so, resolve it and reset the confirmation wait timer in case `LotBidConfirmation` mounts after the challenge or the bid already auto-completed.
  3. **Post-confirmation window**: During `confirmation_wait_seconds` after clicking `Confirmar puja`, poll for the appearance of the "Press & Hold" modal. If detected, invoke `_solve_press_and_hold()` and then wait `post_challenge_wait_seconds` so Catawiki's automatic replay of the held bid updates the DOM/network state before calling `extract_snapshot()`.
- **Rationale**: Covers every point where PerimeterX can intercept the interaction without adding latency to normal unchallenged bids (the post-confirm wait already sleeps `confirmation_wait_seconds`).

## Risks / Trade-offs

- **[Challenge appears with < 4 seconds left on auction clock]** → The default `snipe_window_seconds` is 25.0s, which provides ~19–20 seconds of margin when entering Assault Mode sniping. If a challenge triggers during the final 60 seconds and resolves before 0s, Catawiki automatically extends the auction by +90s.
- **[Simulated test pages without `page.mouse` or `bounding_box()`]** → Support both real Playwright `page.mouse` + `bounding_box()` and a graceful fallback hook on simulated elements in unit tests so existing and new test fixtures remain lightweight and fast.
