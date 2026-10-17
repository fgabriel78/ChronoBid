"""Tests para CatawikiLotPage: parseo DOM/JSON bilingüe y envío de puja mínima sobre páginas simuladas."""

from typing import Any

import pytest

from chronobid.browser.catawiki_page import (
    CatawikiLotPage,
    parse_countdown_seconds,
    parse_euro_amount,
    parse_network_bidding_payload,
)
from chronobid.models.auction import ReserveStatus


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
