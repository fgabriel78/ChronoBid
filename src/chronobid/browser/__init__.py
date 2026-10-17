"""Motor de navegador CDP e interacción con Catawiki."""

from chronobid.browser.catawiki_page import (
    CatawikiLotPage,
    CatawikiSelectors,
    LotPageSnapshot,
    compute_catawiki_next_min_bid,
    parse_countdown_seconds,
    parse_euro_amount,
    parse_network_bidding_payload,
)
from chronobid.browser.cdp_client import (
    BrowserConnectionError,
    CDPBrowserClient,
    SessionNotAuthenticatedError,
    build_browser_launch_command,
)

__all__ = [
    "BrowserConnectionError",
    "CDPBrowserClient",
    "CatawikiLotPage",
    "CatawikiSelectors",
    "LotPageSnapshot",
    "SessionNotAuthenticatedError",
    "build_browser_launch_command",
    "compute_catawiki_next_min_bid",
    "parse_countdown_seconds",
    "parse_euro_amount",
    "parse_network_bidding_payload",
]
