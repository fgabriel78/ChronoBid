"""Tests para CatawikiLotPage: parseo DOM/JSON bilingüe y envío de puja mínima sobre páginas simuladas."""

from typing import Any

import pytest

from chronobid.browser.catawiki_page import (
    CatawikiLotPage,
    CatawikiPageLoadError,
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
    """Fixture de página simulada de lote en Catawiki que actualiza su DOM al recibir una puja."""

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

    # 1. Lectura inicial: no somos líderes, reserva no alcanzada, faltan 19s
    snap0 = await lot_page.extract_snapshot()
    assert snap0.title == "Omega Speedmaster Reduced Automatic"
    assert snap0.current_bid_eur == 240
    assert snap0.next_min_bid_eur == 260
    assert snap0.seconds_remaining == 19.0
    assert snap0.is_leader is False
    assert snap0.reserve_status == ReserveStatus.NOT_MET

    # 2. Enviamos primera puja mínima (260 €) -> pasamos a líder pero reserva sigue en 280 €
    snap1 = await lot_page.submit_minimum_bid(260)
    assert sim_page.submitted_bids == [260]
    assert sim_page.confirm_clicks == 1
    assert snap1.is_leader is True
    assert snap1.current_bid_eur == 260
    assert snap1.next_min_bid_eur == 280
    assert snap1.reserve_status == ReserveStatus.NOT_MET

    # 3. Escalera de reserva: subimos un escalón más (280 €) -> se desbloquea la reserva
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


def test_parse_network_bidding_payload_native_catawiki() -> None:
    payload = {
        "biddingBlockResponse": {
            "title": "Seiko Prospex Speedtimer SBDL091",
            "localizedCurrentBidAmount": 260,
            "localizedMinBidAmount": 280,
            "reservePriceMet": False,
            "closed": False,
            "biddingEndTime": 1792261655000,
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
    """Página simulada con soporte de evaluate para __NEXT_DATA__ y selectores DOM reales."""

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
            "biddingEndTime": 1792261655000,
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
    lot_page = CatawikiLotPage(sim, confirmation_wait_seconds=0.0)

    snap = await lot_page.extract_snapshot()
    assert snap.title == "Seiko Prospex Speedtimer SBDL091"
    assert snap.current_bid_eur == 260
    assert snap.next_min_bid_eur == 280
    assert snap.reserve_status == ReserveStatus.NOT_MET
    assert snap.is_closed is False

    # Envío de puja mínima usando botón rápido dentro de [data-testid="quick-bid-buttons"] button
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
    # 1. Sin reserva explícito en título
    payload_title_no_reserve = {
        "title": "Duward - Automatic - Sin precio de reserva - 1970",
        "reservePriceMet": None,
        "localizedCurrentBidAmount": "10 €",
        "localizedMinBidAmount": "15 €",
    }
    snap1 = parse_network_bidding_payload(payload_title_no_reserve)
    assert snap1 is not None
    assert snap1.reserve_status == ReserveStatus.NO_RESERVE

    # 2. Sin reserva en inglés
    payload_en_no_reserve = {
        "title": "Vintage Chronograph - No reserve price",
        "hasReservePrice": False,
        "localizedCurrentBidAmount": "50 €",
    }
    snap2 = parse_network_bidding_payload(payload_en_no_reserve)
    assert snap2 is not None
    assert snap2.reserve_status == ReserveStatus.NO_RESERVE

    # 3. Con reserva no alcanzada (reservePriceMet: False)
    payload_reserve_not_met = {
        "title": "Omega Seamaster",
        "hasReservePrice": True,
        "reservePriceMet": False,
        "localizedCurrentBidAmount": "200 €",
    }
    snap3 = parse_network_bidding_payload(payload_reserve_not_met)
    assert snap3 is not None
    assert snap3.reserve_status == ReserveStatus.NOT_MET

    # 4. Con reserva alcanzada (reservePriceMet: True)
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
    # Payload representativo de lote sin pujas previas
    payload_starting = {
        "title": "Duward Automatic",
        "localizedCurrentBidAmount": 0,
        "localizedStartBidAmount": 1,
        "localizedMinBidAmount": 1,
        "quickBids": [1, 2, 3],
        "biddingHistory": {"bids": []},
        "biddingEndTime": 1792774276000,
        "reservePriceMet": None,
    }
    snap = parse_network_bidding_payload(payload_starting)
    assert snap is not None
    assert snap.current_bid_eur == 0
    assert snap.next_min_bid_eur == 1
    assert snap.reserve_status == ReserveStatus.NO_RESERVE
    assert snap.extra.get("has_no_bids") is True


async def test_duward_starting_lot_workflow_not_discarded() -> None:
    # Simulación de la página completa de Catawiki para el lote Duward (apertura en 1 € sin pujas)
    class SimulatedDuwardPage:
        def __init__(self) -> None:
            self.title = "Duward - Automatic Day Date Blue dial - Sin precio de reserva - Hombre - 1970-1979"
            self.bidding_block = {
                "localizedCurrentBidAmount": 0,
                "localizedStartBidAmount": 1,
                "localizedMinBidAmount": 1,
                "quickBids": [1, 2, 3],
                "biddingHistory": {"bids": []},
                "biddingEndTime": 1792774276000,
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

    # Validar que WatchLot NO queda descartado como PERDIDO: Superó Máximo con max_bid_eur = 5 €
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
    """Verifica que una pantalla con 'We can't load this page right now' lanza CatawikiPageLoadError y no corrompe el título."""

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

    # El selector de título debe descartar el h1 de error
    title = await lot_page._first_text(lot_page.selectors.title_selectors)
    assert title is None

    # extract_snapshot debe lanzar CatawikiPageLoadError
    with pytest.raises(CatawikiPageLoadError, match="Página de error detectada"):
        await lot_page.extract_snapshot()


@pytest.mark.asyncio
async def test_missing_countdown_preserves_none_seconds_remaining() -> None:
    """Verifica que si la cuenta atrás no está presente en una subasta abierta, seconds_remaining es None y no 0.0."""

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
            # Sin selectores de countdown
            return SimulatedLocator([])

    sim = SimulatedOpenLotWithoutCountdown()
    lot_page = CatawikiLotPage(sim, confirmation_wait_seconds=0.0)

    snap = await lot_page.extract_snapshot()
    assert snap.title == "Seiko Presage SPB045J1"
    assert snap.current_bid_eur == 250
    assert snap.next_min_bid_eur == 270
    assert snap.is_closed is False
    assert snap.seconds_remaining is None  # Debe ser None, NUNCA 0.0


@pytest.mark.asyncio
async def test_blank_page_without_auction_data_raises_load_error() -> None:
    """Verifica que una página vacía o sin datos de subasta lanza CatawikiPageLoadError."""

    class SimulatedEmptyPage:
        def locator(self, selector: str) -> SimulatedLocator:
            return SimulatedLocator([])

    sim = SimulatedEmptyPage()
    lot_page = CatawikiLotPage(sim, confirmation_wait_seconds=0.0)

    with pytest.raises(CatawikiPageLoadError, match="No se encontraron elementos de subasta"):
        await lot_page.extract_snapshot()




