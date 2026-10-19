"""Tests for CLI options and dashboard rendering."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from chronobid.cli import build_dashboard_renderable, build_parser
from chronobid.models.auction import WatchLot
from chronobid.strategy.ledger import BudgetLedger


def test_parser_exposes_log_dir_option() -> None:
    parser = build_parser()
    assert "--log-dir" in parser.format_help()
    args = parser.parse_args(["relojes.xlsx", "--log-dir", "C:/tmp/logs"])
    assert args.log_dir == "C:/tmp/logs"
    assert parser.parse_args(["relojes.xlsx"]).log_dir is None


def _render(renderable) -> str:
    console = Console(record=True, width=200)
    console.print(renderable)
    return console.export_text()


def test_dashboard_shows_session_log_path() -> None:
    lot = WatchLot(lot_id="lot-1", url="https://www.catawiki.com/es/l/1001-omega", max_bid_eur=350)
    log_path = Path("logs") / "chronobid-20261019-192030.log"
    text = _render(build_dashboard_renderable([lot], BudgetLedger(1500), log_path))
    assert "Log de sesión" in text
    assert "chronobid-20261019-192030.log" in text


def test_dashboard_without_log_path_has_no_log_line() -> None:
    lot = WatchLot(lot_id="lot-1", url="https://www.catawiki.com/es/l/1001-omega", max_bid_eur=350)
    text = _render(build_dashboard_renderable([lot], BudgetLedger(1500)))
    assert "Log de sesión" not in text
