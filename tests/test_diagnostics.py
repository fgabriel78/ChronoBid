"""Tests for per-session diagnostic logging and decision tracing."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from chronobid.diagnostics import (
    DecisionTracer,
    default_log_dir,
    setup_session_logging,
    teardown_session_logging,
)
from chronobid.models.auction import BidDecision, WatchLot
from chronobid.strategy.ledger import BudgetLedger


@pytest.fixture(autouse=True)
def _cleanup_logging():
    yield
    teardown_session_logging()


def test_session_log_file_created_next_to_excel_with_timestamp(tmp_path: Path) -> None:
    excel = tmp_path / "relojes.xlsx"
    excel.write_bytes(b"")
    path = setup_session_logging(excel, now=datetime(2026, 10, 19, 19, 20, 30))

    assert path == default_log_dir(excel) / "chronobid-20261019-192030.log"
    assert path is not None and path.exists()

    logging.getLogger("chronobid.orchestrator").info("hola sesión")
    for h in logging.getLogger("chronobid").handlers:
        h.flush()
    assert "hola sesión" in path.read_text(encoding="utf-8")
    # Never propagated to the root logger (keeps the Rich dashboard clean)
    assert logging.getLogger("chronobid").propagate is False


def test_custom_log_dir(tmp_path: Path) -> None:
    path = setup_session_logging(tmp_path / "x.xlsx", log_dir=tmp_path / "custom")
    assert path is not None and path.parent == tmp_path / "custom"


def test_unwritable_log_dir_returns_none_and_warns_once(tmp_path: Path) -> None:
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file, not dir")
    messages: list[str] = []
    path = setup_session_logging(
        tmp_path / "x.xlsx", log_dir=blocker / "logs", on_error=messages.append
    )
    assert path is None
    assert len(messages) == 1


def test_repeated_setup_replaces_previous_handler(tmp_path: Path) -> None:
    setup_session_logging(tmp_path / "x.xlsx", now=datetime(2026, 1, 1, 0, 0, 0))
    setup_session_logging(tmp_path / "x.xlsx", now=datetime(2026, 1, 1, 0, 0, 1))
    session_handlers = [
        h for h in logging.getLogger("chronobid").handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(session_handlers) == 1


def _lot(seconds: float, my_bid: int = 0) -> WatchLot:
    return WatchLot(
        lot_id="lot-1",
        url="https://www.catawiki.com/es/l/1001-omega",
        max_bid_eur=500,
        current_bid_eur=200,
        next_min_bid_eur=220,
        seconds_remaining=seconds,
        my_last_bid_eur=my_bid,
    )


def test_decision_tracer_suppresses_duplicates_outside_window(caplog) -> None:
    tracer = DecisionTracer(snipe_window_seconds=25.0)
    ledger = BudgetLedger(1000)
    caplog.set_level(logging.INFO, logger="chronobid.decisions")

    d1 = BidDecision(should_bid=False, reason="Invisibilidad previa activa: 120.0s > 25s")
    d2 = BidDecision(should_bid=False, reason="Invisibilidad previa activa: 119.7s > 25s")
    assert tracer.log_decision(_lot(120.0), d1, ledger) is True
    assert tracer.log_decision(_lot(119.7), d2, ledger) is False

    d3 = BidDecision(should_bid=False, reason="Bloqueado por techo dinámico/presupuesto")
    assert tracer.log_decision(_lot(100.0), d3, ledger) is True


def test_decision_tracer_logs_every_tick_inside_snipe_and_overtime_windows(caplog) -> None:
    tracer = DecisionTracer(snipe_window_seconds=25.0)
    ledger = BudgetLedger(1000)
    caplog.set_level(logging.INFO, logger="chronobid.decisions")
    same = BidDecision(should_bid=False, reason="Líder provisional")

    assert tracer.log_decision(_lot(20.0), same, ledger) is True
    assert tracer.log_decision(_lot(19.0), same, ledger) is True
    # Overtime counter window applies only once we have bid
    assert tracer.log_decision(_lot(80.0, my_bid=220), same, ledger) is True
    assert tracer.log_decision(_lot(79.0, my_bid=220), same, ledger) is True

    records = [r for r in caplog.records if r.name == "chronobid.decisions"]
    assert len(records) == 4
    assert "secs=20.0" in records[0].getMessage()
    assert "next_min=220" in records[0].getMessage()
