"""Tests for PortfolioOptimizer: live pruning, multi-watch reservation, and dynamic ceiling."""

from chronobid.models.auction import AuctionStatus, WatchLot
from chronobid.strategy.ledger import BudgetLedger
from chronobid.strategy.optimizer import PortfolioOptimizer


def test_optimizer_prunes_lots_exceeding_max_bid() -> None:
    optimizer = PortfolioOptimizer()
    lot_ok = WatchLot(
        lot_id="lot-ok",
        url="https://www.catawiki.com/es/l/1001-seiko",
        max_bid_eur=300,
        current_bid_eur=180,
        next_min_bid_eur=200,
    )
    lot_exceeded = WatchLot(
        lot_id="lot-exceeded",
        url="https://www.catawiki.com/es/l/1002-omega",
        max_bid_eur=300,
        current_bid_eur=300,
        next_min_bid_eur=320,
    )

    pruned = optimizer.prune_lots([lot_ok, lot_exceeded])
    assert len(pruned) == 1
    assert pruned[0].lot_id == "lot-exceeded"
    assert lot_exceeded.status == AuctionStatus.DISCARDED_MAX_EXCEEDED
    assert lot_exceeded.dynamic_ceiling_eur == 0
    assert lot_ok.status == AuctionStatus.PENDING


def test_optimizer_reserves_budget_for_two_cheap_future_watches_with_high_slack() -> None:
    """
    Scenario: Global budget = 600 €.
    Active watch 1 has maximum M_1 = 550 €.
    There are two cheap future watches with high slack:
      - Watch 2: next minimum = 100 €, maximum = 250 € -> H_2 = 0.60 >= 0.15, C_hat_2 = 145 €
      - Watch 3: next minimum = 120 €, maximum = 300 € -> H_3 = 0.60 >= 0.15, C_hat_3 = 174 €
    Reserved sum = 319 €.
    Watch 1's dynamic ceiling is reduced to 600 - 319 = 281 € (< 550 €)
    to avoid starving the combination of 2 future watches.
    """
    ledger = BudgetLedger(global_budget_eur=600)
    optimizer = PortfolioOptimizer(alpha=0.30, min_slack_ratio=0.15)

    lot_active = WatchLot(
        lot_id="lot-1",
        url="https://www.catawiki.com/es/l/2001-longines",
        max_bid_eur=550,
        current_bid_eur=200,
        next_min_bid_eur=220,
        seconds_remaining=20,
    )
    lot_future_1 = WatchLot(
        lot_id="lot-2",
        url="https://www.catawiki.com/es/l/2002-seiko",
        max_bid_eur=250,
        current_bid_eur=90,
        next_min_bid_eur=100,
        seconds_remaining=900,
    )
    lot_future_2 = WatchLot(
        lot_id="lot-3",
        url="https://www.catawiki.com/es/l/2003-citizen",
        max_bid_eur=300,
        current_bid_eur=110,
        next_min_bid_eur=120,
        seconds_remaining=1200,
    )

    ceiling = optimizer.compute_dynamic_ceiling(
        lot_active,
        [lot_active, lot_future_1, lot_future_2],
        ledger,
    )
    assert ceiling == 281
    assert lot_active.dynamic_ceiling_eur == 281
    assert ceiling < lot_active.max_bid_eur


def test_optimizer_releases_full_ceiling_when_future_watches_lose_slack_or_get_pruned() -> None:
    """
    Scenario: When future watches increase in price and their slack H_j drops below
    H_min (0.15) or exceeds their maximum, the optimizer releases the reservation and raises
    the active watch's dynamic ceiling up to min(M_i, available).
    """
    ledger = BudgetLedger(global_budget_eur=600)
    optimizer = PortfolioOptimizer(alpha=0.30, min_slack_ratio=0.15)

    lot_active = WatchLot(
        lot_id="lot-1",
        url="https://www.catawiki.com/es/l/3001-longines",
        max_bid_eur=550,
        current_bid_eur=260,
        next_min_bid_eur=280,
        seconds_remaining=18,
    )
    # Future watch 2 rises to 235 € over 250 € (H_2 = 15/250 = 0.06 < 0.15)
    lot_future_1 = WatchLot(
        lot_id="lot-2",
        url="https://www.catawiki.com/es/l/3002-seiko",
        max_bid_eur=250,
        current_bid_eur=220,
        next_min_bid_eur=235,
        seconds_remaining=600,
    )
    # Future watch 3 exceeds its maximum (320 > 300) -> automatically pruned
    lot_future_2 = WatchLot(
        lot_id="lot-3",
        url="https://www.catawiki.com/es/l/3003-citizen",
        max_bid_eur=300,
        current_bid_eur=300,
        next_min_bid_eur=320,
        seconds_remaining=800,
    )

    ceiling = optimizer.compute_dynamic_ceiling(
        lot_active,
        [lot_active, lot_future_1, lot_future_2],
        ledger,
    )
    assert lot_future_2.status == AuctionStatus.DISCARDED_MAX_EXCEEDED
    assert ceiling == 550
    assert lot_active.dynamic_ceiling_eur == 550
