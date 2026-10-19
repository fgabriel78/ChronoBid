"""Per-session diagnostic logging (file-based, dashboard-safe) and tactical decision tracing."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from chronobid.models.auction import BidDecision, WatchLot
from chronobid.strategy.ledger import BudgetLedger

LOGGER_NAME = "chronobid"
LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_SESSION_HANDLER_FLAG = "_chronobid_session_handler"
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

decision_logger = logging.getLogger(f"{LOGGER_NAME}.decisions")


def default_log_dir(excel_path: str | Path) -> Path:
    """Returns the default `logs` directory located next to the Excel file."""
    return Path(excel_path).resolve().parent / "logs"


def setup_session_logging(
    excel_path: str | Path,
    log_dir: str | Path | None = None,
    *,
    now: datetime | None = None,
    on_error: Callable[[str], None] | None = None,
) -> Path | None:
    """
    Opens a new timestamped log file for this session and attaches it to the `chronobid`
    logger at DEBUG level. No console handler is added and propagation to the root logger
    is disabled so the Rich live dashboard is never polluted.

    Returns the log file path, or None if the directory/file cannot be created (in which
    case `on_error` is invoked once with a human-readable message and monitoring continues).
    """
    target_dir = Path(log_dir) if log_dir is not None else default_log_dir(excel_path)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    log_path = target_dir / f"chronobid-{stamp}.log"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError as exc:
        if on_error is not None:
            on_error(f"No se pudo crear el fichero de log en {target_dir}: {exc}")
        return None

    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    setattr(handler, _SESSION_HANDLER_FLAG, True)

    teardown_session_logging()
    root = logging.getLogger(LOGGER_NAME)
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    root.propagate = False
    return log_path


def teardown_session_logging() -> None:
    """Detaches and closes any session file handler and restores propagation."""
    root = logging.getLogger(LOGGER_NAME)
    for handler in list(root.handlers):
        if getattr(handler, _SESSION_HANDLER_FLAG, False):
            root.removeHandler(handler)
            handler.close()
    root.propagate = True


class DecisionTracer:
    """
    Records tactical decisions per lot. Inside the snipe or overtime counter window every
    evaluation is logged; outside it, identical consecutive decisions (same outcome and
    reason, ignoring numeric values such as remaining seconds) are suppressed.
    """

    def __init__(
        self,
        *,
        snipe_window_seconds: float,
        overtime_window_seconds: float = 95.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.snipe_window_seconds = snipe_window_seconds
        self.overtime_window_seconds = overtime_window_seconds
        self._logger = logger or decision_logger
        self._last_key: dict[str, tuple[object, ...]] = {}

    def in_trace_window(self, lot: WatchLot) -> bool:
        secs = lot.seconds_remaining
        if secs is None:
            return False
        if secs <= self.snipe_window_seconds:
            return True
        return lot.my_last_bid_eur > 0 and secs <= self.overtime_window_seconds

    def log_decision(self, lot: WatchLot, decision: BidDecision, ledger: BudgetLedger) -> bool:
        """Logs the decision unless it is a duplicate outside the trace window. Returns True if logged."""
        key = (
            decision.should_bid,
            decision.bid_amount_eur,
            _NUMBER_RE.sub("#", decision.reason or ""),
        )
        in_window = self.in_trace_window(lot)
        if not in_window and self._last_key.get(lot.lot_id) == key:
            return False
        self._last_key[lot.lot_id] = key
        secs = lot.seconds_remaining
        self._logger.info(
            "DECISION lot=%s row=%s secs=%s current=%d next_min=%d leader=%s ceiling=%d "
            "available=%d bid=%s amount=%s window=%s reason=%s",
            lot.lot_id,
            lot.row_index,
            f"{secs:.1f}" if secs is not None else "None",
            lot.current_bid_eur,
            lot.next_min_bid_eur,
            lot.is_leader,
            decision.dynamic_ceiling_eur,
            ledger.available_eur,
            decision.should_bid,
            decision.bid_amount_eur,
            in_window,
            decision.reason,
        )
        return True

    def forget(self, lot_id: str) -> None:
        self._last_key.pop(lot_id, None)
