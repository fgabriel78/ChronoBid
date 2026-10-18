"""Lot status extractor and bid executor on Catawiki (DOM + JSON network)."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from chronobid.models.auction import ReserveStatus


class CatawikiPageLoadError(Exception):
    """Exception raised when a Catawiki page returns a transient error or is not a valid lot."""

    pass


@dataclass(frozen=True)
class CatawikiSelectors:
    """Configurable bilingual (ES/EN) DOM selectors and text patterns for Catawiki."""

    title_selectors: tuple[str, ...] = (
        '[data-testid="lot-title"]',
        "h1[data-testid='lot-title']",
        "h1.be-lot-title",
        "h1",
    )
    current_bid_selectors: tuple[str, ...] = (
        '[data-testid="lot-bid-status-current-bid"]',
        '[data-testid="lot-bid-status-box"] [data-testid="lot-bid-status-current-bid"]',
        '[data-testid="current-bid-amount"]',
        '[data-testid="lot-bid-status-section"] [data-testid="amount"]',
        '[data-testid="bid-amount"]',
        ".be-lot-bid-status__amount",
    )
    next_min_bid_selectors: tuple[str, ...] = (
        '[data-testid="quick-bid-buttons"] button',
        '[data-testid="next-minimum-bid"]',
        '[data-testid="quick-bid-button"]',
        'input[name="bid"]',
        'input[data-testid="bid-input"]',
        'input[name="bid_amount"]',
    )
    countdown_selectors: tuple[str, ...] = (
        '[data-testid="lot-bidding-counter"]',
        '[data-testid="timer-countdown"]',
        "time[datetime]",
    )
    bid_input_selectors: tuple[str, ...] = (
        'input[name="bid"]',
        'input[data-testid="bid-input"]',
        'input[name="bid_amount"]',
        'input[type="number"]',
    )
    bid_submit_selectors: tuple[str, ...] = (
        '[data-sentry-component="BidInputRow"] button.c-button--secondary',
        'form:has(input[name="bid"]) button.c-button--secondary',
        '[data-sentry-component="BidInputRow"] button:has-text("Pujar")',
        '[data-sentry-component="BidInputRow"] button:has-text("Place bid")',
        'button[data-testid="place-bid-button"]',
        'button[data-testid="bid-submit-button"]',
        'form:has(input[name="bid"]) button[type="submit"]',
    )
    bid_confirm_selectors: tuple[str, ...] = (
        'article[role="dialog"][data-sentry-component="LotBidConfirmation"] button.c-button--primary',
        'button[data-sentry-source-file*="LotBidConfirmation"].c-button--primary',
        'article[role="dialog"] footer button.c-button--primary',
        '[role="dialog"] button:has-text("Confirmar puja")',
        '[role="dialog"] button:has-text("Confirm bid")',
        'button[data-testid="confirm-bid-button"]',
        'button[data-testid="modal-confirm-bid"]',
    )
    bid_cancel_selectors: tuple[str, ...] = (
        'article[role="dialog"][data-sentry-component="LotBidConfirmation"] button.c-button--tertiary',
        'button[data-sentry-source-file*="LotBidConfirmation"].c-button--tertiary',
        '[role="dialog"] button:has-text("Cancelar")',
        '[role="dialog"] button:has-text("Cancel")',
    )
    leader_phrases: tuple[str, ...] = (
        "eres el máximo pujador",
        "tu puja es la más alta",
        "vas en cabeza",
        "tienes la puja más alta",
        "you are the highest bidder",
        "you're the highest bidder",
        "highest bidder",
    )
    reserve_not_met_phrases: tuple[str, ...] = (
        "precio de reserva no alcanzado",
        "no se ha alcanzado el precio de reserva",
        "reserve price not met",
        "reserve price has not been met",
    )
    reserve_met_phrases: tuple[str, ...] = (
        "precio de reserva alcanzado",
        "se ha alcanzado el precio de reserva",
        "reserve price met",
        "reserve price has been met",
    )
    no_reserve_phrases: tuple[str, ...] = (
        "sin precio de reserva",
        "sin reserva",
        "no reserve price",
        "no reserve",
    )
    no_bids_phrases: tuple[str, ...] = (
        "no hay ninguna puja",
        "empezar a pujar",
        "no bids yet",
        "start bidding",
    )
    closed_phrases: tuple[str, ...] = (
        "subasta cerrada",
        "lote cerrado",
        "subasta finalizada",
        "bidding closed",
        "auction closed",
        "lot closed",
    )
    error_phrases: tuple[str, ...] = (
        "we can't load this page right now",
        "we cannot load this page right now",
        "no podemos cargar esta página en este momento",
        "no podemos cargar esta página",
        "something went wrong",
        "algo ha salido mal",
        "página no encontrada",
        "page not found",
        "access denied",
        "just a moment...",
    )


@dataclass
class LotPageSnapshot:
    """Structured snapshot of live state for a Catawiki lot page."""

    title: str = ""
    current_bid_eur: int = 0
    next_min_bid_eur: int = 0
    seconds_remaining: float | None = None
    closing_time: datetime | None = None
    is_leader: bool = False
    reserve_status: ReserveStatus = ReserveStatus.NO_RESERVE
    is_closed: bool = False
    raw_source: str = "dom"
    extra: dict[str, Any] = field(default_factory=dict)


def compute_catawiki_next_min_bid(current_bid_eur: int) -> int:
    """
    Computes standard Catawiki next minimum bid step in euros
    according to the platform's official increment tier table.
    """
    if current_bid_eur <= 0:
        return 1
    if current_bid_eur <= 10:
        return current_bid_eur + 1
    if current_bid_eur <= 100:
        return current_bid_eur + 5
    if current_bid_eur <= 200:
        return current_bid_eur + 10
    if current_bid_eur <= 500:
        return current_bid_eur + 20
    if current_bid_eur <= 1000:
        return current_bid_eur + 50
    if current_bid_eur <= 2000:
        return current_bid_eur + 100
    if current_bid_eur <= 5000:
        return current_bid_eur + 200
    if current_bid_eur <= 10000:
        return current_bid_eur + 500
    if current_bid_eur <= 20000:
        return current_bid_eur + 1000
    if current_bid_eur <= 50000:
        return current_bid_eur + 2000
    return current_bid_eur + 5000


def parse_euro_amount(text: str | int | float | None) -> int | None:
    """Extracts an integer euro amount from text such as '€ 320', '320 €', '1.250 €', or '1,250 EUR'."""
    if text is None:
        return None
    if isinstance(text, int):
        return text if text >= 0 else None
    if isinstance(text, float):
        return int(round(text)) if text >= 0 else None

    raw = str(text).strip()
    if not raw:
        return None

    # Search for numeric sequences with possible thousand separators (. or ,)
    match = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d+)", raw)
    if not match:
        return None
    digits = re.sub(r"[.,]", "", match.group(1))
    try:
        return int(digits)
    except ValueError:
        return None


def parse_countdown_seconds(text: str | int | float | None) -> float | None:
    """
    Converts a countdown timer into remaining seconds.
    Supports:
      - Direct seconds: '18s', '85', 42.5
      - Clock format: '01:29', '00:04:15', '1d 02:10:00', '2h 15m 10s', '04m 12s'
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return max(0.0, float(text))

    raw = str(text).strip().lower()
    if not raw:
        return None

    # Pure number or ending in 's'
    if re.fullmatch(r"\d+(?:\.\d+)?s?", raw):
        return max(0.0, float(raw.rstrip("s")))

    # HH:MM:SS or MM:SS format
    colon_match = re.search(r"(?:(\d+):)?(\d{1,2}):(\d{2})", raw)
    if colon_match:
        hours = int(colon_match.group(1) or 0)
        minutes = int(colon_match.group(2))
        seconds = int(colon_match.group(3))
        days_match = re.search(r"(\d+)\s*d", raw)
        days = int(days_match.group(1)) if days_match else 0
        return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)

    # Format with explicit units: Xd Xh Xm Xs
    total = 0.0
    found = False
    for pattern, factor in (
        (r"(\d+)\s*d", 86400),
        (r"(\d+)\s*h", 3600),
        (r"(\d+)\s*m(?:in)?", 60),
        (r"(\d+(?:\.\d+)?)\s*s(?:eg|ec)?", 1),
    ):
        m = re.search(pattern, raw)
        if m:
            total += float(m.group(1)) * factor
            found = True

    return total if found else None


def parse_network_bidding_payload(payload: dict[str, Any]) -> LotPageSnapshot | None:
    """
    Extracts a LotPageSnapshot from a Catawiki network JSON payload
    (or nested under 'biddingBlockResponse' / 'bidding_block' / 'lot' / 'data').
    """
    if not isinstance(payload, dict):
        return None

    root: dict[str, Any] = payload
    for key in ("biddingBlockResponse", "bidding_block", "lot", "data"):
        if isinstance(root.get(key), dict):
            root = root[key]

    live_lot_bid = None
    if isinstance(root.get("live"), dict):
        lot_obj = root["live"].get("lot", {})
        if isinstance(lot_obj, dict):
            bid_dict = lot_obj.get("bid", {})
            if isinstance(bid_dict, dict):
                live_lot_bid = bid_dict.get("EUR") or bid_dict.get("eur")

    cur_bid = parse_euro_amount(
        root.get("localizedCurrentBidAmount")
        or live_lot_bid
        or root.get("current_bid_amount")
        or root.get("current_bid")
        or root.get("highest_bid_amount")
    )
    first_quick_bid = None
    if isinstance(root.get("quickBids"), list) and root["quickBids"]:
        first_quick_bid = root["quickBids"][0]

    next_min = parse_euro_amount(
        root.get("localizedMinBidAmount")
        or root.get("localizedStartBidAmount")
        or first_quick_bid
        or root.get("next_minimum_bid")
        or root.get("next_min_bid")
        or root.get("minimum_bid_amount")
    )

    seconds_rem: float | None = None
    if "biddingEndTime" in root and isinstance(root["biddingEndTime"], (int, float)):
        end_ms = float(root["biddingEndTime"])
        now_ms = datetime.now(timezone.utc).timestamp() * 1000.0
        seconds_rem = max(0.0, (end_ms - now_ms) / 1000.0)
    elif isinstance(root.get("live"), dict) and "lot" in root["live"]:
        live_lot = root["live"]["lot"]
        if isinstance(live_lot, dict) and "biddingEndTime" in live_lot:
            end_ms = float(live_lot["biddingEndTime"])
            now_ms = datetime.now(timezone.utc).timestamp() * 1000.0
            seconds_rem = max(0.0, (end_ms - now_ms) / 1000.0)

    if seconds_rem is None:
        seconds_rem = parse_countdown_seconds(
            root.get("seconds_remaining")
            or root.get("time_remaining")
            or root.get("remaining_seconds")
        )

    if cur_bid is None and next_min is None and seconds_rem is None:
        return None

    # Detection of opening lots (no bids registered yet)
    has_bids = True
    bids_list = (
        root.get("biddingHistory", {}).get("bids")
        if isinstance(root.get("biddingHistory"), dict)
        else None
    )
    if bids_list is not None and len(bids_list) == 0:
        has_bids = False
    elif root.get("localizedCurrentBidAmount") == 0 and (cur_bid == 0 or cur_bid is None):
        has_bids = False

    if not has_bids:
        cur_val = 0
        next_val = next_min if (next_min is not None and next_min > 0) else 1
    else:
        cur_val = cur_bid if cur_bid is not None else 0
        next_val = (
            next_min
            if (next_min is not None and next_min > 0)
            else compute_catawiki_next_min_bid(cur_val)
        )
    sec_val = seconds_rem if seconds_rem is not None else 0.0

    is_leader = bool(
        root.get("is_highest_bidder")
        or root.get("is_leader")
        or root.get("user_is_highest_bidder")
        or (root.get("isABidder") and root.get("isWinner"))
    )
    is_closed = bool(root.get("is_closed") or root.get("closed") or root.get("bidding_closed"))

    title_str = str(root.get("title", "")).lower()
    has_reserve = (
        root.get("hasReservePrice")
        if "hasReservePrice" in root
        else root.get("has_reserve_price")
    )
    reserve_met = (
        root.get("reservePriceMet")
        if "reservePriceMet" in root
        else root.get("reserve_price_met")
    )

    if (
        "sin precio de reserva" in title_str
        or "no reserve price" in title_str
        or "sin reserva" in title_str
        or "no reserve" in title_str
        or has_reserve is False
        or (has_reserve is None and reserve_met is None)
    ):
        reserve_status = ReserveStatus.NO_RESERVE
    elif reserve_met is True:
        reserve_status = ReserveStatus.MET
    elif reserve_met is False or has_reserve is True:
        reserve_status = ReserveStatus.NOT_MET
    else:
        reserve_status = ReserveStatus.NO_RESERVE

    closing_time = (
        datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=sec_val)
        if sec_val > 0 and not is_closed
        else None
    )

    return LotPageSnapshot(
        title=str(root.get("title", "")),
        current_bid_eur=cur_val,
        next_min_bid_eur=next_val,
        seconds_remaining=sec_val,
        closing_time=closing_time,
        is_leader=is_leader,
        reserve_status=reserve_status,
        is_closed=is_closed,
        raw_source="network_json",
        extra={"has_no_bids": not has_bids},
    )


class CatawikiLotPage:
    """
    Controller for a Catawiki lot tab.
    Combines network JSON response interception and DOM reading with bilingual
    selectors (ES/EN), and executes minimum increment bid submission.
    """

    def __init__(
        self,
        page: Any,
        *,
        selectors: CatawikiSelectors | None = None,
        confirmation_wait_seconds: float = 0.45,
        confirm_appear_timeout_seconds: float = 2.5,
        confirm_poll_interval_seconds: float = 0.05,
    ) -> None:
        self.page = page
        self.selectors = selectors or CatawikiSelectors()
        self.confirmation_wait_seconds = confirmation_wait_seconds
        self.confirm_appear_timeout_seconds = confirm_appear_timeout_seconds
        self.confirm_poll_interval_seconds = confirm_poll_interval_seconds
        self._last_network_snapshot: LotPageSnapshot | None = None
        self._listener_attached = False

    def attach_network_listener(self) -> None:
        """Subscribes a listener to page JSON responses to capture bid updates."""
        if self._listener_attached or not hasattr(self.page, "on"):
            return
        self.page.on("response", self._on_network_response)
        self._listener_attached = True

    async def _on_network_response(self, response: Any) -> None:
        try:
            url = str(getattr(response, "url", "")).lower()
            if not any(token in url for token in ("bid", "lot", "bidding", "status")):
                return
            data = await response.json()
            if isinstance(data, dict):
                snap = parse_network_bidding_payload(data)
                if snap is not None:
                    self._last_network_snapshot = snap
        except Exception:
            return

    async def navigate(self, url: str, *, timeout_ms: int = 20000) -> None:
        """Navigates to the lot URL and attaches the network interceptor."""
        self.attach_network_listener()
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    async def _first_text(self, selectors: tuple[str, ...]) -> str | None:
        for sel in selectors:
            try:
                loc = self.page.locator(sel)
                if await loc.count() > 0:
                    first = loc.first
                    text = await first.inner_text()
                    if text and str(text).strip():
                        val = str(text).strip()
                        if any(phrase in val.lower() for phrase in self.selectors.error_phrases):
                            continue
                        return val
            except Exception:
                continue
        return None

    async def _first_attr_or_value(
        self,
        selectors: tuple[str, ...],
        attrs: tuple[str, ...] = ("data-amount", "data-seconds", "value", "datetime"),
    ) -> str | None:
        for sel in selectors:
            try:
                loc = self.page.locator(sel)
                if await loc.count() > 0:
                    first = loc.first
                    for attr in attrs:
                        val = await first.get_attribute(attr)
                        if val is not None and str(val).strip():
                            return str(val).strip()
                    text = await first.inner_text()
                    if text and str(text).strip():
                        val_str = str(text).strip()
                        if any(phrase in val_str.lower() for phrase in self.selectors.error_phrases):
                            continue
                        return val_str
            except Exception:
                continue
        return None

    async def _extract_next_data_snapshot(self) -> LotPageSnapshot | None:
        """Extracts typed snapshot from Next.js <script id="__NEXT_DATA__"> tag."""
        try:
            if not hasattr(self.page, "evaluate"):
                return None
            data = await self.page.evaluate('''() => {
                const el = document.getElementById('__NEXT_DATA__');
                if (!el) return null;
                try {
                    const parsed = JSON.parse(el.textContent || el.innerText || '{}');
                    return parsed.props?.pageProps?.biddingBlockResponse || null;
                } catch (e) {
                    return null;
                }
            }''')
            if isinstance(data, dict):
                snap = parse_network_bidding_payload(data)
                if snap is not None:
                    snap.raw_source = "next_data_ssr"
                    return snap
        except Exception:
            return None
        return None

    async def extract_snapshot(self) -> LotPageSnapshot:
        """Extracts current lot state combining Next.js SSR, DOM, and JSON network reading."""
        # 1. Full visible page text (or main container) to detect ES/EN errors and notices
        body_text = ""
        try:
            body_loc = self.page.locator("body")
            if await body_loc.count() > 0:
                body_text = (await body_loc.first.inner_text()).lower()
        except Exception:
            body_text = ""

        # Early detection of Catawiki error pages
        for phrase in self.selectors.error_phrases:
            if phrase in body_text:
                raise CatawikiPageLoadError(f"Página de error detectada en Catawiki: '{phrase}'")

        # 2. Attempt direct extraction from Next.js __NEXT_DATA__ (initial / hydrated state)
        ssr_snap = await self._extract_next_data_snapshot()

        # 3. Direct reading of DOM elements
        title = (await self._first_text(self.selectors.title_selectors)) or ""

        raw_current_bid = await self._first_attr_or_value(
            self.selectors.current_bid_selectors,
            attrs=("data-amount", "value"),
        )
        current_bid = parse_euro_amount(raw_current_bid)

        raw_next_min = await self._first_attr_or_value(
            self.selectors.next_min_bid_selectors,
            attrs=("data-next-min", "data-amount", "value", "placeholder"),
        )
        next_min = parse_euro_amount(raw_next_min)

        raw_countdown = await self._first_attr_or_value(
            self.selectors.countdown_selectors,
            attrs=("data-seconds", "data-remaining-seconds"),
        )
        seconds_rem = parse_countdown_seconds(raw_countdown)

        # Fallback to last network snapshot if available
        net = self._last_network_snapshot

        is_closed = any(phrase in body_text for phrase in self.selectors.closed_phrases)
        if not is_closed and ssr_snap is not None and ssr_snap.is_closed:
            is_closed = True
        elif not is_closed and net is not None and not body_text:
            is_closed = net.is_closed

        dom_has_no_bids = any(phrase in body_text for phrase in self.selectors.no_bids_phrases)
        dom_is_leader = any(phrase in body_text for phrase in self.selectors.leader_phrases)
        ssr_start_min = ssr_snap.next_min_bid_eur if ssr_snap is not None else 1
        dom_shows_active_bids = bool(body_text) and not dom_has_no_bids and (
            dom_is_leader
            or (current_bid is not None and current_bid > 1)
            or (next_min is not None and next_min > ssr_start_min)
            or (
                net is not None
                and not net.extra.get("has_no_bids", False)
                and net.current_bid_eur > 0
            )
        )

        # Detection of opening lots without bids
        is_opening_lot = not dom_shows_active_bids and (
            dom_has_no_bids
            or (ssr_snap is not None and ssr_snap.extra.get("has_no_bids", False))
            or (net is not None and net.extra.get("has_no_bids", False))
        )

        # Validation that the page contains genuine auction data
        has_auction_data = (
            ssr_snap is not None
            or net is not None
            or current_bid is not None
            or next_min is not None
            or seconds_rem is not None
            or is_opening_lot
            or is_closed
        )
        if not has_auction_data:
            raise CatawikiPageLoadError(
                "No se encontraron elementos de subasta (puja, contador o estado) en la página de Catawiki."
            )

        sec_val = seconds_rem

        if ssr_snap is not None:
            if not title and ssr_snap.title:
                title = ssr_snap.title
            if current_bid is None or (ssr_snap.current_bid_eur > current_bid and not is_opening_lot):
                current_bid = ssr_snap.current_bid_eur
            if next_min is None or (ssr_snap.next_min_bid_eur > (next_min or 0) and not is_opening_lot):
                next_min = ssr_snap.next_min_bid_eur
            if sec_val is None or sec_val == 0.0:
                sec_val = ssr_snap.seconds_remaining

        if current_bid is None and net is not None:
            current_bid = net.current_bid_eur
        elif net is not None and net.current_bid_eur > (current_bid or 0) and not is_opening_lot:
            current_bid = net.current_bid_eur

        if next_min is None and net is not None:
            next_min = net.next_min_bid_eur
        if sec_val is None and net is not None:
            sec_val = net.seconds_remaining
        if not title and net is not None:
            title = net.title

        if is_opening_lot:
            # For lots without prior bids, if DOM or SSR display the starting bid (e.g., 1 €),
            # the real current market bid is 0 € and required opening bid is the starting bid
            # (1 € or the first quick-bid button).
            if current_bid is not None and (current_bid <= 1 or (ssr_snap and ssr_snap.current_bid_eur == 0)):
                current_bid = 0
            if next_min is None or next_min <= 0:
                next_min = 1

        cur_val = current_bid if current_bid is not None else 0
        if is_opening_lot and next_min is not None and next_min > 0:
            next_val = next_min
        else:
            next_val = (
                next_min
                if (next_min is not None and next_min > cur_val)
                else compute_catawiki_next_min_bid(cur_val)
            )

        is_leader = dom_is_leader
        if not is_leader and ssr_snap is not None and ssr_snap.is_leader:
            is_leader = True
        elif not is_leader and net is not None and not body_text:
            is_leader = net.is_leader

        full_text = f"{title.lower()} {body_text}"
        if any(phrase in full_text for phrase in self.selectors.no_reserve_phrases):
            reserve_status = ReserveStatus.NO_RESERVE
        elif any(phrase in body_text for phrase in self.selectors.reserve_not_met_phrases):
            reserve_status = ReserveStatus.NOT_MET
        elif any(phrase in body_text for phrase in self.selectors.reserve_met_phrases):
            reserve_status = ReserveStatus.MET
        elif ssr_snap is not None:
            reserve_status = ssr_snap.reserve_status
        elif net is not None and not body_text:
            reserve_status = net.reserve_status
        else:
            reserve_status = ReserveStatus.NO_RESERVE

        closing_time = None
        if ssr_snap is not None and ssr_snap.closing_time is not None:
            closing_time = ssr_snap.closing_time
        elif sec_val is not None and sec_val > 0 and not is_closed:
            closing_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=sec_val)

        raw_source = "hybrid"
        if ssr_snap is not None and current_bid == ssr_snap.current_bid_eur:
            raw_source = "next_data_ssr"
        elif net is not None and current_bid == net.current_bid_eur:
            raw_source = "network_json"
        else:
            raw_source = "dom"

        return LotPageSnapshot(
            title=title,
            current_bid_eur=cur_val,
            next_min_bid_eur=next_val,
            seconds_remaining=0.0 if is_closed else sec_val,
            closing_time=closing_time,
            is_leader=is_leader,
            reserve_status=reserve_status,
            is_closed=is_closed,
            raw_source=raw_source,
        )

    async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
        """
        Executes a bid for the exact amount `bid_amount_eur` in Catawiki interface,
        actively waits for confirmation modal dialog (`LotBidConfirmation`) to mount,
        clicks primary confirm button, and returns new snapshot after update.
        """
        if bid_amount_eur <= 0:
            raise ValueError("El importe de puja debe ser mayor que 0 EUR.")

        # 0. If a previous hanging confirmation modal exists, close it before starting
        for cancel_sel in self.selectors.bid_cancel_selectors:
            try:
                cancel_loc = self.page.locator(cancel_sel)
                if await cancel_loc.count() > 0:
                    await cancel_loc.first.click()
                    break
            except Exception:
                continue

        # 1. Attempt quick-bid button if it matches bid_amount_eur exactly
        quick_clicked = False
        try:
            for quick_sel in ('[data-testid="quick-bid-buttons"] button', '[data-testid="quick-bid-button"]'):
                quick_loc = self.page.locator(quick_sel)
                count = await quick_loc.count()
                if count > 0:
                    for idx in range(count):
                        btn = quick_loc.nth(idx)
                        btn_text = (await btn.get_attribute("data-amount")) or (await btn.inner_text())
                        if parse_euro_amount(btn_text) == bid_amount_eur:
                            await btn.click()
                            quick_clicked = True
                            break
                if quick_clicked:
                    break
        except Exception:
            quick_clicked = False

        # 2. If no exact quick button, fill bid input and click submit (direct bid)
        if not quick_clicked:
            filled = False
            for input_sel in self.selectors.bid_input_selectors:
                loc = self.page.locator(input_sel)
                if await loc.count() > 0:
                    await loc.first.fill(str(bid_amount_eur))
                    filled = True
                    break
            if not filled:
                raise RuntimeError(
                    "No se encontró el campo de entrada de puja ni botón rápido en la página de Catawiki."
                )

            submitted = False
            for submit_sel in self.selectors.bid_submit_selectors:
                loc = self.page.locator(submit_sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    submitted = True
                    break
            if not submitted:
                raise RuntimeError("No se encontró el botón de envío de puja en la página de Catawiki.")

        # 3. Actively wait for confirmation modal dialog to appear and click confirm
        confirmed = False
        elapsed = 0.0
        timeout = max(0.0, self.confirm_appear_timeout_seconds)
        poll = max(0.01, self.confirm_poll_interval_seconds)

        while True:
            for confirm_sel in self.selectors.bid_confirm_selectors:
                try:
                    conf_loc = self.page.locator(confirm_sel)
                    if await conf_loc.count() > 0:
                        await conf_loc.first.click()
                        confirmed = True
                        break
                except Exception:
                    continue
            if confirmed or elapsed >= timeout:
                break
            await asyncio.sleep(poll)
            elapsed += poll

        if self.confirmation_wait_seconds > 0:
            await asyncio.sleep(self.confirmation_wait_seconds)

        return await self.extract_snapshot()
