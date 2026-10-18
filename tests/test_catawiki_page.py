"""Tests for CatawikiLotPage: bilingual DOM/JSON parsing and minimum bid submission on simulated pages."""

from typing import Any

import pytest

from chronobid.browser.catawiki_page import (
    CatawikiLotPage,
    CatawikiPageLoadError,
    CatawikiSelectors,
    compute_catawiki_next_min_bid,
    parse_countdown_seconds,
    parse_euro_amount,
    parse_network_bidding_payload,
)
from chronobid.models.auction import AuctionStatus, ReserveStatus, WatchLot


class SimulatedElement:
    def __init__(
        self,
        text: str = "",
        attrs: dict[str, str] | None = None,
        on_click: Any = None,
        on_fill: Any = None,
    ) -> None:
        self.text = text
        self.attrs = attrs or {}
        self._on_click = on_click
        self._on_fill = on_fill

    async def inner_text(self) -> str:
        return self.text

    async def get_attribute(self, name: str) -> str | None:
        return self.attrs.get(name)

    async def fill(self, value: str) -> None:
        self.attrs["value"] = value
        if self._on_fill:
            self._on_fill(value)

    async def click(self) -> None:
        if self._on_click:
            self._on_click()


class SimulatedLocator:
    def __init__(self, elements: list[SimulatedElement]) -> None:
        self._elements = elements

    async def count(self) -> int:
        return len(self._elements)

    @property
    def first(self) -> SimulatedElement:
        return self._elements[0]

    def nth(self, index: int) -> SimulatedElement:
        return self._elements[index]


class SimulatedCatawikiDOMPage:
    """Simulated Catawiki lot page fixture that updates its DOM upon receiving a bid."""

    def __init__(
        self,
        *,
        title: str = "Omega Speedmaster Reduced Automatic",
        current_bid: int = 240,
        next_min_bid: int = 260,
        countdown_text: str = "00:19",
        is_leader: bool = False,
        reserve_not_met: bool = True,
        closed: bool = False,
        lang: str = "es",
    ) -> None:
        self.title = title
        self.current_bid = current_bid
        self.next_min_bid = next_min_bid
        self.countdown_text = countdown_text
        self.is_leader = is_leader
        self.reserve_not_met = reserve_not_met
        self.closed = closed
        self.lang = lang
        self.filled_amount: int | None = None
        self.submitted_bids: list[int] = []
        self.confirm_clicks: int = 0
        self.reserve_threshold: int = 280

    def _apply_bid(self, amount: int) -> None:
        self.submitted_bids.append(amount)
        self.current_bid = amount
        self.next_min_bid = amount + 20
        self.is_leader = True
        if amount >= self.reserve_threshold:
            self.reserve_not_met = False

    def locator(self, selector: str) -> SimulatedLocator:
        if selector == '[data-testid="lot-title"]':
            return SimulatedLocator([SimulatedElement(text=self.title)])
        if selector == '[data-testid="current-bid-amount"]':
            return SimulatedLocator([SimulatedElement(text=f"€ {self.current_bid}")])
        if selector == '[data-testid="next-minimum-bid"]':
            return SimulatedLocator([SimulatedElement(text=f"{self.next_min_bid} €")])
        if selector == '[data-testid="lot-bidding-counter"]':
            return SimulatedLocator([SimulatedElement(text=self.countdown_text)])
        if selector == 'input[data-testid="bid-input"]':
            return SimulatedLocator(
                [
                    SimulatedElement(
                        attrs={"value": ""},
                        on_fill=lambda val: setattr(self, "filled_amount", int(val)),
                    )
                ]
            )
        if selector == 'button[data-testid="place-bid-button"]':
            return SimulatedLocator(
                [
                    SimulatedElement(
                        text="Pujar",
                        on_click=lambda: self._apply_bid(self.filled_amount or self.next_min_bid),
                    )
                ]
            )
        if selector == 'button[data-testid="confirm-bid-button"]':
            return SimulatedLocator(
                [
                    SimulatedElement(
                        text="Confirmar",
                        on_click=lambda: setattr(self, "confirm_clicks", self.confirm_clicks + 1),
                    )
                ]
            )
        if selector == "body":
            parts = [self.title]
            if self.is_leader:
                parts.append(
                    "Eres el máximo pujador"
                    if self.lang == "es"
                    else "You are the highest bidder"
                )
            if self.reserve_not_met:
                parts.append(
                    "Precio de reserva no alcanzado"
                    if self.lang == "es"
                    else "Reserve price not met"
                )
            else:
                parts.append(
                    "Precio de reserva alcanzado"
                    if self.lang == "es"
                    else "Reserve price met"
                )
            if self.closed:
                parts.append("Subasta cerrada" if self.lang == "es" else "Bidding closed")
            return SimulatedLocator([SimulatedElement(text=" | ".join(parts))])
        return SimulatedLocator([])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("€ 320", 320),
        ("1.250 €", 1250),
        ("1,400 EUR", 1400),
        (450, 450),
        ("", None),
    ],
)
def test_parse_euro_amount(raw: str | int, expected: int | None) -> None:
    assert parse_euro_amount(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("18s", 18.0),
        ("01:30", 90.0),
        ("00:04:10", 250.0),
        ("2m 15s", 135.0),
        (24, 24.0),
    ],
)
def test_parse_countdown_seconds(raw: str | int, expected: float) -> None:
    assert parse_countdown_seconds(raw) == expected


def test_parse_network_bidding_payload() -> None:
    payload = {
        "bidding_block": {
            "title": "Longines Conquest Automatic",
            "current_bid_amount": 310,
            "next_minimum_bid": 330,
            "seconds_remaining": 14,
            "is_highest_bidder": True,
            "reserve_price_met": False,
            "is_closed": False,
        }
    }
    snap = parse_network_bidding_payload(payload)
    assert snap is not None
    assert snap.title == "Longines Conquest Automatic"
    assert snap.current_bid_eur == 310
    assert snap.next_min_bid_eur == 330
    assert snap.seconds_remaining == 14.0
    assert snap.is_leader is True
    assert snap.reserve_status == ReserveStatus.NOT_MET


async def test_catawiki_lot_page_extract_and_submit_bid_with_reserve_ladder() -> None:
    sim_page = SimulatedCatawikiDOMPage(
        current_bid=240,
        next_min_bid=260,
        countdown_text="00:19",
        is_leader=False,
        reserve_not_met=True,
        lang="es",
    )
    sim_page.reserve_threshold = 280

    lot_page = CatawikiLotPage(sim_page, confirmation_wait_seconds=0.0)

    # 1. Initial read: not leader, reserve not met, 19s remaining
    snap0 = await lot_page.extract_snapshot()
    assert snap0.title == "Omega Speedmaster Reduced Automatic"
    assert snap0.current_bid_eur == 240
    assert snap0.next_min_bid_eur == 260
    assert snap0.seconds_remaining == 19.0
    assert snap0.is_leader is False
    assert snap0.reserve_status == ReserveStatus.NOT_MET

    # 2. Submit first minimum bid (260 €) -> become leader but reserve remains at 280 €
    snap1 = await lot_page.submit_minimum_bid(260)
    assert sim_page.submitted_bids == [260]
    assert sim_page.confirm_clicks == 1
    assert snap1.is_leader is True
    assert snap1.current_bid_eur == 260
    assert snap1.next_min_bid_eur == 280
    assert snap1.reserve_status == ReserveStatus.NOT_MET

    # 3. Reserve ladder: raise one more step (280 €) -> reserve is unlocked
    snap2 = await lot_page.submit_minimum_bid(280)
    assert sim_page.submitted_bids == [260, 280]
    assert snap2.is_leader is True
    assert snap2.current_bid_eur == 280
    assert snap2.reserve_status == ReserveStatus.MET


async def test_catawiki_lot_page_english_locale_and_closed_detection() -> None:
    sim_page_en = SimulatedCatawikiDOMPage(
        current_bid=400,
        next_min_bid=420,
        countdown_text="00:00",
        is_leader=True,
        reserve_not_met=False,
        closed=True,
        lang="en",
    )
    lot_page = CatawikiLotPage(sim_page_en, confirmation_wait_seconds=0.0)
    snap = await lot_page.extract_snapshot()
    assert snap.is_leader is True
    assert snap.reserve_status == ReserveStatus.MET
    assert snap.is_closed is True
    assert snap.seconds_remaining == 0.0


def test_catawiki_selectors_updated_definitions() -> None:
    from chronobid.browser.catawiki_page import CatawikiSelectors

    selectors = CatawikiSelectors()
    assert '[data-testid="lot-bid-status-current-bid"]' in selectors.current_bid_selectors
    assert 'input[name="bid"]' in selectors.bid_input_selectors
    assert '[data-testid="quick-bid-buttons"] button' in selectors.next_min_bid_selectors
    assert (
        'article[role="dialog"][data-sentry-component="LotBidConfirmation"] button.c-button--primary'
        in selectors.bid_confirm_selectors
    )
    assert '[role="dialog"] button:has-text("Confirmar puja")' in selectors.bid_confirm_selectors
    assert (
        '[data-sentry-component="BidInputRow"] button.c-button--secondary'
        in selectors.bid_submit_selectors
    )


def test_parse_network_bidding_payload_native_catawiki() -> None:
    payload = {
        "biddingBlockResponse": {
            "title": "Seiko Prospex Speedtimer SBDL091",
            "localizedCurrentBidAmount": 260,
            "localizedMinBidAmount": 280,
            "reservePriceMet": False,
            "closed": False,
            "biddingEndTime": 1992261655000,
            "quickBids": [280, 300, 360],
            "live": {
                "lot": {
                    "id": 107402108,
                    "bid": {"EUR": 260},
                }
            },
        }
    }
    snap = parse_network_bidding_payload(payload)
    assert snap is not None
    assert snap.title == "Seiko Prospex Speedtimer SBDL091"
    assert snap.current_bid_eur == 260
    assert snap.next_min_bid_eur == 280
    assert snap.reserve_status == ReserveStatus.NOT_MET
    assert snap.is_closed is False
    assert snap.seconds_remaining > 0


class SimulatedNextDataAndDOMPage:
    """Simulated page with evaluate support for __NEXT_DATA__ and real DOM selectors."""

    def __init__(
        self,
        *,
        title: str = "Seiko Prospex Speedtimer SBDL091",
        current_bid_dom: str = " € 260",
        next_data_bid: int = 260,
        next_data_min: int = 280,
    ) -> None:
        self.title = title
        self.current_bid_dom = current_bid_dom
        self.next_data_bid = next_data_bid
        self.next_data_min = next_data_min
        self.clicked_quick_buttons: list[str] = []
        self.filled_inputs: list[str] = []

    async def evaluate(self, script: str) -> Any:
        return {
            "title": self.title,
            "localizedCurrentBidAmount": self.next_data_bid,
            "localizedMinBidAmount": self.next_data_min,
            "reservePriceMet": False,
            "closed": False,
            "biddingEndTime": 1992261655000,
        }

    def locator(self, selector: str) -> SimulatedLocator:
        if selector in ('[data-testid="lot-title"]', "h1"):
            return SimulatedLocator([SimulatedElement(text=self.title)])
        if selector == '[data-testid="lot-bid-status-current-bid"]':
            return SimulatedLocator([SimulatedElement(text=self.current_bid_dom)])
        if selector == '[data-testid="quick-bid-buttons"] button':
            return SimulatedLocator(
                [
                    SimulatedElement(
                        text="€ 280",
                        on_click=lambda: self.clicked_quick_buttons.append("280"),
                    ),
                    SimulatedElement(
                        text="€ 300",
                        on_click=lambda: self.clicked_quick_buttons.append("300"),
                    ),
                ]
            )
        if selector == 'input[name="bid"]':
            return SimulatedLocator(
                [
                    SimulatedElement(
                        attrs={"value": ""},
                        on_fill=lambda v: self.filled_inputs.append(v),
                    )
                ]
            )
        if selector == "body":
            return SimulatedLocator([SimulatedElement(text=f"{self.title} precio de reserva no alcanzado")])
        return SimulatedLocator([])


async def test_catawiki_hybrid_extraction_and_quick_bid() -> None:
    sim = SimulatedNextDataAndDOMPage(current_bid_dom=" € 260", next_data_bid=260, next_data_min=280)
    lot_page = CatawikiLotPage(
        sim,
        confirmation_wait_seconds=0.0,
        confirm_appear_timeout_seconds=0.0,
    )

    snap = await lot_page.extract_snapshot()
    assert snap.title == "Seiko Prospex Speedtimer SBDL091"
    assert snap.current_bid_eur == 260
    assert snap.next_min_bid_eur == 280
    assert snap.reserve_status == ReserveStatus.NOT_MET
    assert snap.is_closed is False

    # Submit minimum bid using quick bid button inside [data-testid="quick-bid-buttons"] button
    await lot_page.submit_minimum_bid(280)
    assert sim.clicked_quick_buttons == ["280"]


@pytest.mark.parametrize(
    ("current_bid", "expected_next"),
    [
        (0, 1),
        (1, 2),
        (5, 6),
        (10, 11),
        (11, 16),
        (50, 55),
        (100, 105),
        (101, 111),
        (200, 210),
        (201, 221),
        (500, 520),
        (501, 551),
        (1000, 1050),
        (1001, 1101),
        (2000, 2100),
        (2500, 2700),
        (5000, 5200),
        (6000, 6500),
        (10000, 10500),
        (12000, 13000),
        (25000, 27000),
        (60000, 65000),
    ],
)
def test_compute_catawiki_next_min_bid(current_bid: int, expected_next: int) -> None:
    assert compute_catawiki_next_min_bid(current_bid) == expected_next


def test_reserve_status_detection() -> None:
    # 1. Explicit no reserve in title
    payload_title_no_reserve = {
        "title": "Duward - Automatic - Sin precio de reserva - 1970",
        "reservePriceMet": None,
        "localizedCurrentBidAmount": "10 €",
        "localizedMinBidAmount": "15 €",
    }
    snap1 = parse_network_bidding_payload(payload_title_no_reserve)
    assert snap1 is not None
    assert snap1.reserve_status == ReserveStatus.NO_RESERVE

    # 2. No reserve in English
    payload_en_no_reserve = {
        "title": "Vintage Chronograph - No reserve price",
        "hasReservePrice": False,
        "localizedCurrentBidAmount": "50 €",
    }
    snap2 = parse_network_bidding_payload(payload_en_no_reserve)
    assert snap2 is not None
    assert snap2.reserve_status == ReserveStatus.NO_RESERVE

    # 3. With reserve not met (reservePriceMet: False)
    payload_reserve_not_met = {
        "title": "Omega Seamaster",
        "hasReservePrice": True,
        "reservePriceMet": False,
        "localizedCurrentBidAmount": "200 €",
    }
    snap3 = parse_network_bidding_payload(payload_reserve_not_met)
    assert snap3 is not None
    assert snap3.reserve_status == ReserveStatus.NOT_MET

    # 4. With reserve met (reservePriceMet: True)
    payload_reserve_met = {
        "title": "Omega Seamaster",
        "hasReservePrice": True,
        "reservePriceMet": True,
        "localizedCurrentBidAmount": "500 €",
    }
    snap4 = parse_network_bidding_payload(payload_reserve_met)
    assert snap4 is not None
    assert snap4.reserve_status == ReserveStatus.MET


def test_starting_lot_extraction_network_payload() -> None:
    # Representative payload for lot with no previous bids
    payload_starting = {
        "title": "Duward Automatic",
        "localizedCurrentBidAmount": 0,
        "localizedStartBidAmount": 1,
        "localizedMinBidAmount": 1,
        "quickBids": [1, 2, 3],
        "biddingHistory": {"bids": []},
        "biddingEndTime": 1992774276000,
        "reservePriceMet": None,
    }
    snap = parse_network_bidding_payload(payload_starting)
    assert snap is not None
    assert snap.current_bid_eur == 0
    assert snap.next_min_bid_eur == 1
    assert snap.reserve_status == ReserveStatus.NO_RESERVE
    assert snap.extra.get("has_no_bids") is True


async def test_duward_starting_lot_workflow_not_discarded() -> None:
    # Full Catawiki page simulation for Duward lot (opening at 1 € with no bids)
    class SimulatedDuwardPage:
        def __init__(self) -> None:
            self.title = "Duward - Automatic Day Date Blue dial - Sin precio de reserva - Hombre - 1970-1979"
            self.bidding_block = {
                "localizedCurrentBidAmount": 0,
                "localizedStartBidAmount": 1,
                "localizedMinBidAmount": 1,
                "quickBids": [1, 2, 3],
                "biddingHistory": {"bids": []},
                "biddingEndTime": 1992774276000,
                "reservePriceMet": None,
                "title": self.title,
            }

        async def evaluate(self, script: str) -> Any:
            if "props.pageProps" in script or "__NEXT_DATA__" in script:
                return {"biddingBlockResponse": self.bidding_block}
            return None

        def locator(self, selector: str) -> SimulatedLocator:
            if selector == '[data-testid="lot-bid-status-current-bid"]':
                return SimulatedLocator([SimulatedElement(text=" € 1")])
            if selector == '[data-testid="quick-bid-buttons"] button':
                return SimulatedLocator(
                    [
                        SimulatedElement(text="€ 1"),
                        SimulatedElement(text="€ 2"),
                        SimulatedElement(text="€ 3"),
                    ]
                )
            if selector == 'input[name="bid"]':
                return SimulatedLocator([SimulatedElement(attrs={"value": "", "placeholder": "1 o más"})])
            if selector == "body":
                return SimulatedLocator([SimulatedElement(text=f"{self.title} EMPEZAR A PUJAR € 1 Sin precio de reserva No hay ninguna puja")])
            return SimulatedLocator([])

    sim = SimulatedDuwardPage()
    lot_page = CatawikiLotPage(sim, confirmation_wait_seconds=0.0)

    snap = await lot_page.extract_snapshot()
    assert snap.current_bid_eur == 0
    assert snap.next_min_bid_eur == 1
    assert snap.reserve_status == ReserveStatus.NO_RESERVE

    # Validate that WatchLot is NOT discarded as DISCARDED_MAX_EXCEEDED with max_bid_eur = 5 €
    lot = WatchLot(
        lot_id="107658268",
        url="https://www.catawiki.com/es/l/107658268-duward-automatic",
        max_bid_eur=5,
        title=snap.title,
    )
    status = lot.apply_market_snapshot(
        current_bid_eur=snap.current_bid_eur,
        next_min_bid_eur=snap.next_min_bid_eur,
        seconds_remaining=snap.seconds_remaining,
        is_leader=snap.is_leader,
        reserve_status=snap.reserve_status,
        is_closed=snap.is_closed,
    )
    assert status == AuctionStatus.PATROL
    assert lot.status != AuctionStatus.DISCARDED_MAX_EXCEEDED
    assert lot.is_viable is True


@pytest.mark.asyncio
async def test_error_page_raises_catawiki_page_load_error() -> None:
    """Verify that a screen with 'We can't load this page right now' raises CatawikiPageLoadError and does not corrupt the title."""

    class SimulatedErrorPage:
        def locator(self, selector: str) -> SimulatedLocator:
            if selector == "h1":
                return SimulatedLocator([SimulatedElement(text="We can't load this page right now")])
            if selector == "body":
                return SimulatedLocator(
                    [SimulatedElement(text="We can't load this page right now. Please try again later.")]
                )
            return SimulatedLocator([])

    sim = SimulatedErrorPage()
    lot_page = CatawikiLotPage(sim, confirmation_wait_seconds=0.0)

    # Title selector must discard error h1
    title = await lot_page._first_text(lot_page.selectors.title_selectors)
    assert title is None

    # extract_snapshot must raise CatawikiPageLoadError
    with pytest.raises(CatawikiPageLoadError, match="Página de error detectada"):
        await lot_page.extract_snapshot()


@pytest.mark.asyncio
async def test_missing_countdown_preserves_none_seconds_remaining() -> None:
    """Verify that if countdown is not present in an open auction, seconds_remaining is None and not 0.0."""

    class SimulatedOpenLotWithoutCountdown:
        def locator(self, selector: str) -> SimulatedLocator:
            if selector == '[data-testid="lot-title"]':
                return SimulatedLocator([SimulatedElement(text="Seiko Presage SPB045J1")])
            if selector == '[data-testid="lot-bid-status-current-bid"]':
                return SimulatedLocator([SimulatedElement(text="250 €")])
            if selector == '[data-testid="next-minimum-bid"]':
                return SimulatedLocator([SimulatedElement(text="270 €")])
            if selector == "body":
                return SimulatedLocator([SimulatedElement(text="seiko presage spb045j1 sin precio de reserva")])
            # No countdown selectors
            return SimulatedLocator([])

    sim = SimulatedOpenLotWithoutCountdown()
    lot_page = CatawikiLotPage(sim, confirmation_wait_seconds=0.0)

    snap = await lot_page.extract_snapshot()
    assert snap.title == "Seiko Presage SPB045J1"
    assert snap.current_bid_eur == 250
    assert snap.next_min_bid_eur == 270
    assert snap.is_closed is False
    assert snap.seconds_remaining is None  # Must be None, NEVER 0.0


@pytest.mark.asyncio
async def test_blank_page_without_auction_data_raises_load_error() -> None:
    """Verify that an empty page or a page without auction data raises CatawikiPageLoadError."""

    class SimulatedEmptyPage:
        def locator(self, selector: str) -> SimulatedLocator:
            return SimulatedLocator([])

    sim = SimulatedEmptyPage()
    lot_page = CatawikiLotPage(sim, confirmation_wait_seconds=0.0)

    with pytest.raises(CatawikiPageLoadError, match="No se encontraron elementos de subasta"):
        await lot_page.extract_snapshot()


@pytest.mark.asyncio
async def test_submit_minimum_bid_waits_for_async_lot_bid_confirmation_modal_and_bid_input_row() -> None:
    """
    Verify that:
      1. When placing a bid (via quick button or numeric field in BidInputRow),
         if the `LotBidConfirmation` modal takes multiple polling passes to mount into the DOM,
         `submit_minimum_bid` actively waits and clicks `button.c-button--primary` ('Confirmar puja').
      2. For numeric field bidding, it selects `[data-sentry-component="BidInputRow"] button.c-button--secondary`
         ('Pujar') and never 'Fijar puja máx'.
    """

    class SimulatedAsyncModalPage:
        def __init__(self) -> None:
            self.title = "Duward - Automatic - Sin precio de reserva"
            self.current_bid = 0
            self.next_min_bid = 1
            self.is_leader = False
            self.pending_bid: int | None = None
            self.modal_polls_remaining = 0
            self.confirm_clicks = 0
            self.direct_pujar_clicks = 0
            self.filled_bid_str: str | None = None

        async def evaluate(self, script: str) -> Any:
            # Initial static __NEXT_DATA__ at 0 € (no bids when opening tab)
            return {
                "biddingBlockResponse": {
                    "title": self.title,
                    "localizedCurrentBidAmount": 0,
                    "localizedStartBidAmount": 1,
                    "localizedMinBidAmount": 1,
                    "quickBids": [1, 2, 3],
                    "biddingHistory": {"bids": []},
                    "biddingEndTime": 1992774276000,
                    "reservePriceMet": None,
                }
            }

        def _open_modal_after_delay(self, amount: int) -> None:
            self.pending_bid = amount
            # The modal will not be available on first query (0 ms), but after 2 poll cycles
            self.modal_polls_remaining = 2

        def _confirm_modal(self) -> None:
            assert self.pending_bid is not None
            self.confirm_clicks += 1
            self.current_bid = self.pending_bid
            self.next_min_bid = self.pending_bid + 1
            self.is_leader = True
            self.pending_bid = None

        def locator(self, selector: str) -> SimulatedLocator:
            if selector == '[data-testid="lot-title"]':
                return SimulatedLocator([SimulatedElement(text=self.title)])
            if selector == '[data-testid="lot-bid-status-current-bid"]':
                shown = 1 if self.current_bid == 0 else self.current_bid
                return SimulatedLocator([SimulatedElement(text=f" € {shown}")])
            if selector == '[data-testid="quick-bid-buttons"] button':
                return SimulatedLocator(
                    [
                        SimulatedElement(
                            text=f"€ {self.next_min_bid}",
                            on_click=lambda: self._open_modal_after_delay(self.next_min_bid),
                        ),
                    ]
                )
            if selector == 'input[name="bid"]':
                return SimulatedLocator(
                    [
                        SimulatedElement(
                            attrs={"value": ""},
                            on_fill=lambda val: setattr(self, "filled_bid_str", val),
                        )
                    ]
                )
            if selector == '[data-sentry-component="BidInputRow"] button.c-button--secondary':
                return SimulatedLocator(
                    [
                        SimulatedElement(
                            text="Pujar",
                            on_click=lambda: (
                                setattr(self, "direct_pujar_clicks", self.direct_pujar_clicks + 1),
                                self._open_modal_after_delay(int(self.filled_bid_str or "0")),
                            ),
                        )
                    ]
                )
            if (
                selector
                == 'article[role="dialog"][data-sentry-component="LotBidConfirmation"] button.c-button--primary'
            ):
                if self.pending_bid is not None:
                    if self.modal_polls_remaining > 0:
                        self.modal_polls_remaining -= 1
                        return SimulatedLocator([])
                    return SimulatedLocator(
                        [
                            SimulatedElement(
                                text="Confirmar puja",
                                on_click=self._confirm_modal,
                            )
                        ]
                    )
                return SimulatedLocator([])
            if selector == "body":
                if self.is_leader:
                    return SimulatedLocator(
                        [
                            SimulatedElement(
                                text=f"{self.title} PUJA ACTUAL € {self.current_bid} Eres el máximo pujador"
                            )
                        ]
                    )
                return SimulatedLocator(
                    [
                        SimulatedElement(
                            text=f"{self.title} EMPEZAR A PUJAR € 1 Sin precio de reserva No hay ninguna puja"
                        )
                    ]
                )
            return SimulatedLocator([])

    sim = SimulatedAsyncModalPage()
    lot_page = CatawikiLotPage(
        sim,
        confirmation_wait_seconds=0.0,
        confirm_appear_timeout_seconds=0.5,
        confirm_poll_interval_seconds=0.01,
    )

    # 1. Initial state before bidding: opening lot at 0 € with minimum 1 €
    snap0 = await lot_page.extract_snapshot()
    assert snap0.current_bid_eur == 0
    assert snap0.next_min_bid_eur == 1
    assert snap0.is_leader is False

    # 2. Bid 1 € via quick button -> waits for LotBidConfirmation to appear and clicks Confirmar puja
    # Also verifies that after confirming, even if __NEXT_DATA__ remains at 0 €, live DOM governs (1 € and leader)
    snap1 = await lot_page.submit_minimum_bid(1)
    assert sim.confirm_clicks == 1
    assert snap1.is_leader is True
    assert snap1.current_bid_eur == 1
    assert snap1.next_min_bid_eur == 2

    # 3. Bid 5 € (does not match quick button 2 €) -> uses input[name="bid"] + BidInputRow c-button--secondary
    snap2 = await lot_page.submit_minimum_bid(5)
    assert sim.direct_pujar_clicks == 1
    assert sim.confirm_clicks == 2
    assert snap2.is_leader is True
    assert snap2.current_bid_eur == 5
    assert snap2.next_min_bid_eur == 6


def test_catawiki_selectors_press_and_hold_patterns() -> None:
    selectors = CatawikiSelectors()
    assert "#px-captcha iframe" in selectors.press_and_hold_selectors
    assert "#px-captcha" in selectors.press_and_hold_selectors
    assert '[id*="px-captcha"]' in selectors.press_and_hold_selectors
    assert 'button:has-text("Press & Hold")' in selectors.press_and_hold_selectors
    assert '[role="button"]:has-text("Press & Hold")' in selectors.press_and_hold_selectors
    assert 'button:has-text("Mantén pulsado")' in selectors.press_and_hold_selectors
    assert '[role="button"]:has-text("Mantén pulsado")' in selectors.press_and_hold_selectors


async def test_solve_press_and_hold_across_frames_with_mouse_and_bounding_box() -> None:
    events: list[tuple[str, Any]] = []
    polls_until_dismiss = 3

    class SimulatedHoldTarget:
        async def is_visible(self) -> bool:
            return True

        async def bounding_box(self) -> dict[str, float]:
            return {"x": 400.0, "y": 300.0, "width": 200.0, "height": 50.0}

    class SimulatedChildFrame:
        def locator(self, selector: str) -> SimulatedLocator:
            nonlocal polls_until_dismiss
            if selector == 'button:has-text("Press & Hold")':
                if polls_until_dismiss > 0:
                    polls_until_dismiss -= 1
                    return SimulatedLocator([SimulatedHoldTarget()])  # type: ignore[list-item]
            return SimulatedLocator([])

    class SimulatedMouse:
        async def move(self, x: float, y: float, *, steps: int = 1) -> None:
            events.append(("move", (round(x, 1), round(y, 1), steps)))

        async def down(self) -> None:
            events.append(("down", None))

        async def up(self) -> None:
            events.append(("up", None))

    class SimulatedPageWithFrames:
        def __init__(self) -> None:
            self.frames = [SimulatedChildFrame()]
            self.mouse = SimulatedMouse()

        def locator(self, selector: str) -> SimulatedLocator:
            return SimulatedLocator([])

    page = SimulatedPageWithFrames()
    lot_page = CatawikiLotPage(
        page,
        press_and_hold_max_seconds=0.5,
        press_and_hold_poll_seconds=0.01,
    )

    solved = await lot_page._solve_press_and_hold()
    assert solved is True
    assert [e[0] for e in events] == ["move", "down", "up"]
    move_coords = events[0][1]
    assert 500.0 <= move_coords[0] <= 505.0
    assert 325.0 <= move_coords[1] <= 328.0
    assert move_coords[2] == 8


async def test_solve_press_and_hold_guarantees_mouse_up_on_error() -> None:
    events: list[str] = []
    call_count = 0

    class SimulatedHoldTarget:
        async def bounding_box(self) -> dict[str, float]:
            return {"x": 100.0, "y": 100.0, "width": 120.0, "height": 40.0}

    class SimulatedMouse:
        async def move(self, x: float, y: float, *, steps: int = 1) -> None:
            events.append("move")

        async def down(self) -> None:
            events.append("down")

        async def up(self) -> None:
            events.append("up")

    class SimulatedPage:
        def __init__(self) -> None:
            self.mouse = SimulatedMouse()

        def locator(self, selector: str) -> SimulatedLocator:
            nonlocal call_count
            if selector == "#px-captcha":
                call_count += 1
                if call_count == 1:
                    return SimulatedLocator([SimulatedHoldTarget()])  # type: ignore[list-item]
                return SimulatedLocator([])
            return SimulatedLocator([])

    lot_page = CatawikiLotPage(
        SimulatedPage(),
        press_and_hold_max_seconds=0.05,
        press_and_hold_poll_seconds=0.01,
    )
    assert await lot_page._solve_press_and_hold() is True
    assert events == ["move", "down", "up"]


async def test_submit_minimum_bid_resolves_press_and_hold_after_confirm() -> None:
    """
    Simulates Catawiki intercepting the bid after clicking 'Confirmar puja' with a
    'Press & Hold' modal (#px-captcha). Only after continuous mouse hold completes
    does the modal close and Catawiki automatically register the bid.
    """

    class SimulatedHoldElement:
        async def is_visible(self) -> bool:
            return True

        async def bounding_box(self) -> dict[str, float]:
            return {"x": 350.0, "y": 280.0, "width": 220.0, "height": 54.0}

    class SimulatedPressAndHoldLotPage:
        def __init__(self) -> None:
            self.title = "Seiko Chronograph SND195P"
            self.current_bid = 120
            self.next_min_bid = 130
            self.is_leader = False
            self.pending_bid: int | None = None
            self.confirm_visible = False
            self.challenge_active = False
            self.hold_polls_needed = 3
            self.mouse_down = False
            self.mouse_events: list[str] = []
            self.mouse = self

        async def move(self, x: float, y: float, *, steps: int = 1) -> None:
            self.mouse_events.append(f"move:{steps}")

        async def down(self) -> None:
            self.mouse_down = True
            self.mouse_events.append("down")

        async def up(self) -> None:
            self.mouse_down = False
            self.mouse_events.append("up")

        def _on_quick_bid_click(self, amount: int) -> None:
            self.pending_bid = amount
            self.confirm_visible = True

        def _on_confirm_click(self) -> None:
            self.confirm_visible = False
            # Instead of applying bid immediately, PerimeterX challenge mounts
            self.challenge_active = True

        def locator(self, selector: str) -> SimulatedLocator:
            if selector == "#px-captcha":
                if self.challenge_active:
                    if self.mouse_down:
                        self.hold_polls_needed -= 1
                        if self.hold_polls_needed <= 0:
                            self.challenge_active = False
                            # Bid registers automatically once challenge closes
                            if self.pending_bid is not None:
                                self.current_bid = self.pending_bid
                                self.next_min_bid = self.pending_bid + 10
                                self.is_leader = True
                            return SimulatedLocator([])
                    return SimulatedLocator([SimulatedHoldElement()])  # type: ignore[list-item]
                return SimulatedLocator([])
            if selector == '[data-testid="lot-title"]':
                return SimulatedLocator([SimulatedElement(text=self.title)])
            if selector == '[data-testid="current-bid-amount"]':
                return SimulatedLocator([SimulatedElement(text=f"€ {self.current_bid}")])
            if selector == '[data-testid="next-minimum-bid"]':
                return SimulatedLocator([SimulatedElement(text=f"{self.next_min_bid} €")])
            if selector == '[data-testid="lot-bidding-counter"]':
                return SimulatedLocator([SimulatedElement(text="00:16")])
            if selector == '[data-testid="quick-bid-buttons"] button':
                return SimulatedLocator(
                    [
                        SimulatedElement(
                            text=f"€ {self.next_min_bid}",
                            on_click=lambda: self._on_quick_bid_click(self.next_min_bid),
                        )
                    ]
                )
            if selector == 'button[data-testid="confirm-bid-button"]':
                if self.confirm_visible:
                    return SimulatedLocator(
                        [
                            SimulatedElement(
                                text="Confirmar puja",
                                on_click=self._on_confirm_click,
                            )
                        ]
                    )
                return SimulatedLocator([])
            if selector == "body":
                status_txt = "Eres el máximo pujador" if self.is_leader else "Sin precio de reserva"
                return SimulatedLocator([SimulatedElement(text=f"{self.title} | {status_txt}")])
            return SimulatedLocator([])

    sim = SimulatedPressAndHoldLotPage()
    lot_page = CatawikiLotPage(
        sim,
        confirmation_wait_seconds=0.05,
        confirm_appear_timeout_seconds=0.2,
        confirm_poll_interval_seconds=0.01,
        press_and_hold_max_seconds=0.5,
        press_and_hold_poll_seconds=0.01,
        post_challenge_wait_seconds=0.01,
    )

    snap = await lot_page.submit_minimum_bid(130)
    assert sim.challenge_active is False
    assert sim.mouse_down is False
    assert sim.mouse_events == ["move:8", "down", "up"]
    assert snap.is_leader is True
    assert snap.current_bid_eur == 130
    assert snap.next_min_bid_eur == 140


async def test_submit_minimum_bid_resolves_press_and_hold_before_confirm() -> None:
    """
    Simulates a 'Press & Hold' modal appearing immediately after clicking 'Pujar',
    before the confirmation modal or automatic bid completion.
    """

    class SimulatedHoldElement:
        async def bounding_box(self) -> dict[str, float]:
            return {"x": 300.0, "y": 250.0, "width": 200.0, "height": 50.0}

    class SimulatedPreConfirmChallengePage:
        def __init__(self) -> None:
            self.title = "Tissot PRX Powermatic 80"
            self.current_bid = 200
            self.next_min_bid = 220
            self.is_leader = False
            self.challenge_active = False
            self.hold_polls_needed = 2
            self.mouse_down = False
            self.mouse_events: list[str] = []
            self.mouse = self

        async def move(self, x: float, y: float, *, steps: int = 1) -> None:
            self.mouse_events.append("move")

        async def down(self) -> None:
            self.mouse_down = True
            self.mouse_events.append("down")

        async def up(self) -> None:
            self.mouse_down = False
            self.mouse_events.append("up")

        def _on_quick_click(self) -> None:
            self.challenge_active = True

        def locator(self, selector: str) -> SimulatedLocator:
            if selector == 'button:has-text("Press & Hold")':
                if self.challenge_active:
                    if self.mouse_down:
                        self.hold_polls_needed -= 1
                        if self.hold_polls_needed <= 0:
                            self.challenge_active = False
                            self.current_bid = 220
                            self.next_min_bid = 240
                            self.is_leader = True
                            return SimulatedLocator([])
                    return SimulatedLocator([SimulatedHoldElement()])  # type: ignore[list-item]
                return SimulatedLocator([])
            if selector == '[data-testid="lot-title"]':
                return SimulatedLocator([SimulatedElement(text=self.title)])
            if selector == '[data-testid="current-bid-amount"]':
                return SimulatedLocator([SimulatedElement(text=f"€ {self.current_bid}")])
            if selector == '[data-testid="next-minimum-bid"]':
                return SimulatedLocator([SimulatedElement(text=f"{self.next_min_bid} €")])
            if selector == '[data-testid="lot-bidding-counter"]':
                return SimulatedLocator([SimulatedElement(text="00:12")])
            if selector == '[data-testid="quick-bid-buttons"] button':
                return SimulatedLocator(
                    [
                        SimulatedElement(
                            text=f"€ {self.next_min_bid}",
                            on_click=self._on_quick_click,
                        )
                    ]
                )
            if selector == "body":
                status_txt = "Eres el máximo pujador" if self.is_leader else "Sin precio de reserva"
                return SimulatedLocator([SimulatedElement(text=f"{self.title} | {status_txt}")])
            return SimulatedLocator([])

    sim = SimulatedPreConfirmChallengePage()
    lot_page = CatawikiLotPage(
        sim,
        confirmation_wait_seconds=0.01,
        confirm_appear_timeout_seconds=0.2,
        confirm_poll_interval_seconds=0.01,
        press_and_hold_max_seconds=0.5,
        press_and_hold_poll_seconds=0.01,
        post_challenge_wait_seconds=0.01,
    )

    snap = await lot_page.submit_minimum_bid(220)
    assert sim.challenge_active is False
    assert sim.mouse_events == ["move", "down", "up"]
    assert snap.is_leader is True
    assert snap.current_bid_eur == 220
    assert snap.next_min_bid_eur == 240






