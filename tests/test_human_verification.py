"""Tests for 'Press & Hold' human verification handling during navigation, reads and bids."""

from __future__ import annotations

import pytest

from chronobid.browser.catawiki_page import (
    CatawikiLotPage,
    CatawikiPageLoadError,
    HumanVerificationBlockedError,
)
from tests.test_catawiki_page import SimulatedCatawikiDOMPage, SimulatedLocator


class _HoldTarget:
    async def is_visible(self) -> bool:
        return True

    async def bounding_box(self) -> dict[str, float]:
        return {"x": 300.0, "y": 200.0, "width": 200.0, "height": 50.0}


class ChallengedLotPage(SimulatedCatawikiDOMPage):
    """
    Lot page that shows a full-page PerimeterX challenge (#px-captcha) hiding all auction
    data until the mouse is held for `hold_polls_needed` polls. With `unsolvable=True`
    the challenge never goes away.
    """

    def __init__(self, *, unsolvable: bool = False, hold_polls_needed: int = 3, **kwargs) -> None:
        super().__init__(**kwargs)
        self.challenge_active = True
        self.unsolvable = unsolvable
        self.hold_polls_needed = hold_polls_needed
        self.mouse_down = False
        self.mouse_events: list[str] = []
        self.mouse = self
        self.goto_calls: list[str] = []

    async def goto(self, url: str, **kwargs) -> None:
        self.goto_calls.append(url)

    async def move(self, x: float, y: float, *, steps: int = 1) -> None:
        self.mouse_events.append("move")

    async def down(self) -> None:
        self.mouse_down = True
        self.mouse_events.append("down")

    async def up(self) -> None:
        self.mouse_down = False
        self.mouse_events.append("up")

    def locator(self, selector: str) -> SimulatedLocator:
        if selector == "#px-captcha":
            if self.challenge_active:
                if self.mouse_down and not self.unsolvable:
                    self.hold_polls_needed -= 1
                    if self.hold_polls_needed <= 0:
                        self.challenge_active = False
                        return SimulatedLocator([])
                return SimulatedLocator([_HoldTarget()])  # type: ignore[list-item]
            return SimulatedLocator([])
        if self.challenge_active:
            # Full-page block: no auction data is visible behind the challenge
            return SimulatedLocator([])
        return super().locator(selector)


def _lot_page(sim: ChallengedLotPage) -> CatawikiLotPage:
    return CatawikiLotPage(
        sim,
        confirmation_wait_seconds=0.01,
        confirm_appear_timeout_seconds=0.05,
        confirm_poll_interval_seconds=0.01,
        press_and_hold_max_seconds=0.2,
        press_and_hold_poll_seconds=0.01,
        post_challenge_wait_seconds=0.0,
    )


@pytest.mark.asyncio
async def test_navigate_resolves_challenge_shown_when_opening_tab() -> None:
    sim = ChallengedLotPage(current_bid=300, next_min_bid=320, countdown_text="04:30")
    page = _lot_page(sim)

    await page.navigate("https://www.catawiki.com/es/l/107567796-seiko")

    assert sim.goto_calls
    assert sim.challenge_active is False
    assert sim.mouse_events == ["move", "down", "up"]
    snap = await page.extract_snapshot()
    assert snap.current_bid_eur == 300
    assert snap.next_min_bid_eur == 320


@pytest.mark.asyncio
async def test_extract_snapshot_resolves_challenge_before_reading() -> None:
    sim = ChallengedLotPage(current_bid=300, next_min_bid=320, countdown_text="00:40")
    page = _lot_page(sim)

    snap = await page.extract_snapshot()

    assert sim.challenge_active is False
    assert snap.current_bid_eur == 300
    assert snap.seconds_remaining == 40.0


@pytest.mark.asyncio
async def test_unresolved_full_page_challenge_raises_dedicated_error() -> None:
    sim = ChallengedLotPage(unsolvable=True, current_bid=300, next_min_bid=320)
    page = _lot_page(sim)

    with pytest.raises(HumanVerificationBlockedError):
        await page.extract_snapshot()
    # Still a CatawikiPageLoadError, preserving transient-failure semantics
    assert issubclass(HumanVerificationBlockedError, CatawikiPageLoadError)
    assert sim.mouse_events[-1] == "up"


@pytest.mark.asyncio
async def test_navigate_swallows_unresolved_challenge() -> None:
    sim = ChallengedLotPage(unsolvable=True)
    page = _lot_page(sim)
    # Must not raise: the next snapshot extraction reports the block explicitly
    await page.navigate("https://www.catawiki.com/es/l/107567796-seiko")
    assert sim.goto_calls


@pytest.mark.asyncio
async def test_clean_page_has_no_challenge_side_effects() -> None:
    sim = SimulatedCatawikiDOMPage(current_bid=240, next_min_bid=260)
    page = _lot_page(sim)  # type: ignore[arg-type]
    assert await page.ensure_no_challenge() is False
    snap = await page.extract_snapshot()
    assert snap.current_bid_eur == 240


class ChallengeAfterBidPage(SimulatedCatawikiDOMPage):
    """Clean page whose bid confirmation triggers an unsolvable challenge."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.challenge_active = False
        self.mouse = self

    async def move(self, x: float, y: float, *, steps: int = 1) -> None:
        return None

    async def down(self) -> None:
        return None

    async def up(self) -> None:
        return None

    def _apply_bid(self, amount: int) -> None:
        self.challenge_active = True

    def locator(self, selector: str) -> SimulatedLocator:
        if selector == "#px-captcha":
            if self.challenge_active:
                return SimulatedLocator([_HoldTarget()])  # type: ignore[list-item]
            return SimulatedLocator([])
        return super().locator(selector)


@pytest.mark.asyncio
async def test_submit_bid_raises_when_challenge_persists_after_bid() -> None:
    sim = ChallengeAfterBidPage(current_bid=240, next_min_bid=260, countdown_text="00:15")
    page = _lot_page(sim)  # type: ignore[arg-type]

    with pytest.raises(HumanVerificationBlockedError):
        await page.submit_minimum_bid(260)
