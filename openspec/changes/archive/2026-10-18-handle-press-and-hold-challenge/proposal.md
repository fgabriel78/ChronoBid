# Proposal

## Why

During last-second bidding in Assault Mode, Catawiki occasionally triggers a PerimeterX / HUMAN Security anti-bot modal ("Press & Hold to confirm you are a human") upon submitting or confirming a bid. Currently, `CatawikiLotPage.submit_minimum_bid` only performs instantaneous clicks and waits 0.45 seconds after clicking the confirmation button, causing the challenge modal to remain unresolved, the held bid request to never complete, and the orchestrator to prematurely release the budget reservation while subsequent ticks collide with the modal overlay.

## What Changes

- Extend `CatawikiSelectors` with multi-context selectors and text patterns to detect the "Press & Hold" verification challenge (`#px-captcha`, embedded iframes, and "Press & Hold" button elements in both the main DOM and child frames).
- Add automated continuous mouse press-and-hold resolution (`_solve_press_and_hold`) in `CatawikiLotPage` using `page.mouse` (`move`, `down`, poll until challenge dismissal or timeout, and guaranteed `up`) over the target button's bounding box.
- Integrate challenge detection and resolution into `CatawikiLotPage.submit_minimum_bid` before bid interaction, during confirmation modal polling, and during the post-confirmation window so that when Catawiki intercepts the bid with a "Press & Hold" modal, the challenge is completed and the platform's automatic bid replay settles before extracting the post-bid snapshot.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `catawiki-browser-engine`: Update the minimum increment bidding execution requirement to detect and automatically resolve "Press & Hold" verification challenges via continuous pointer hold during bid submission and confirmation, awaiting automatic bid registration before capturing the post-bid snapshot.

## Impact

- `src/chronobid/browser/catawiki_page.py`: `CatawikiSelectors` and `CatawikiLotPage` (`submit_minimum_bid` and helper methods for detecting and holding the challenge button across main DOM and frames).
- `tests/test_catawiki_page.py`: Unit and simulated DOM/mouse tests covering "Press & Hold" challenge appearance before confirmation, after confirmation, bounding box resolution, continuous hold release, and automatic bid completion.
