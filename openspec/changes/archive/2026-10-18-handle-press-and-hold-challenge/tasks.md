# Tasks

## 1. Press & Hold Detection and Continuous Mouse Resolution

- [x] 1.1 Add `press_and_hold_selectors` to `CatawikiSelectors` in `src/chronobid/browser/catawiki_page.py` covering `#px-captcha iframe`, `#px-captcha`, `[id*="px-captcha"]`, and ES/EN "Press & Hold" button selectors, and verify via unit test in `tests/test_catawiki_page.py` that the selectors tuple contains the expected patterns.
- [x] 1.2 Implement `_find_press_and_hold_target` and `_solve_press_and_hold` in `CatawikiLotPage` (`src/chronobid/browser/catawiki_page.py`) to locate the challenge element across the main page and `page.frames`, compute target coordinates from `bounding_box()`, execute `page.mouse.move` + `page.mouse.down`, poll until challenge dismissal or `press_and_hold_max_seconds`, and guarantee `page.mouse.up` in a `finally` block, verifying behavior with unit tests in `tests/test_catawiki_page.py`.

## 2. Integration into Bid Submission Flow

- [x] 2.1 Integrate `_solve_press_and_hold` into `CatawikiLotPage.submit_minimum_bid` (`src/chronobid/browser/catawiki_page.py`) before bid submission, during confirmation modal polling, and during the post-confirmation wait window (awaiting `post_challenge_wait_seconds` after solving so Catawiki's automatic bid registration settles before `extract_snapshot()`), and verify with simulated DOM/mouse tests in `tests/test_catawiki_page.py` covering challenges triggered both before and after clicking confirm.
- [x] 2.2 Run the full test suite (`pytest`) to verify all existing and new unit and orchestrator end-to-end tests pass without regressions.
