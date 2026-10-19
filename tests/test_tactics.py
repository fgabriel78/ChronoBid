"""Unit tests for the tactical engine in tests/test_tactics.py."""

from chronobid.models.auction import AuctionStatus, ReserveStatus, WatchLot
from chronobid.strategy.ledger import BudgetLedger
from chronobid.strategy.optimizer import PortfolioOptimizer
from chronobid.strategy.tactics import TacticalBiddingEngine


def test_tactics_invisibility_before_25_seconds() -> None:
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)
    lot = WatchLot(
        lot_id="lot-1",
        url="https://www.catawiki.com/es/l/1001-omega",
        max_bid_eur=500,
        current_bid_eur=200,
        next_min_bid_eur=220,
        seconds_remaining=42.0,
        is_leader=False,
    )

    decision = engine.evaluate_lot(lot, dynamic_ceiling_eur=500)
    assert decision.should_bid is False
    assert "Invisibilidad" in decision.reason


def test_tactics_first_entry_under_25_seconds_bids_exact_minimum_step() -> None:
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)
    lot = WatchLot(
        lot_id="lot-1",
        url="https://www.catawiki.com/es/l/1001-omega",
        max_bid_eur=500,
        current_bid_eur=200,
        next_min_bid_eur=220,
        seconds_remaining=19.5,
        is_leader=False,
    )

    decision = engine.evaluate_lot(lot, dynamic_ceiling_eur=480)
    assert decision.should_bid is True
    assert decision.bid_amount_eur == 220
    assert decision.is_reserve_step is False


def test_tactics_counterattack_during_90s_overtime_and_abandon_above_ceiling() -> None:
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0, overtime_window_seconds=95.0)
    lot = WatchLot(
        lot_id="lot-1",
        url="https://www.catawiki.com/es/l/1001-omega",
        max_bid_eur=400,
        my_last_bid_eur=220,
        current_bid_eur=240,
        next_min_bid_eur=260,
        seconds_remaining=88.0,  # Overtime (+90s) just triggered by a rival
        is_leader=False,
    )

    # Counterattack within dynamic ceiling
    decision_counter = engine.evaluate_lot(lot, dynamic_ceiling_eur=350)
    assert decision_counter.should_bid is True
    assert decision_counter.bid_amount_eur == 260
    assert "prórroga" in decision_counter.reason

    # The rival bids above dynamic ceiling (360 > 350) -> immediate abandon
    lot.current_bid_eur = 340
    lot.next_min_bid_eur = 360
    decision_abandon = engine.evaluate_lot(lot, dynamic_ceiling_eur=350)
    assert decision_abandon.should_bid is False

    # The rival exceeds the watch's maximum bid (420 > 400) -> prune
    lot.current_bid_eur = 400
    lot.next_min_bid_eur = 420
    decision_prune = engine.evaluate_lot(lot, dynamic_ceiling_eur=400)
    assert decision_prune.should_bid is False
    assert lot.status == AuctionStatus.DISCARDED_MAX_EXCEEDED


def test_tactics_reserve_price_step_by_step_ladder() -> None:
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)
    lot = WatchLot(
        lot_id="lot-res",
        url="https://www.catawiki.com/es/l/2002-zenith",
        max_bid_eur=500,
        my_last_bid_eur=300,
        current_bid_eur=300,
        next_min_bid_eur=330,
        seconds_remaining=60.0,
        is_leader=True,
        reserve_status=ReserveStatus.NOT_MET,
    )

    # With 60s remaining (> 25s), wait without self-bidding yet
    d_wait = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_wait.should_bid is False

    # Enters final 25s with reserve NOT met -> raises a single step to 330 €
    lot.seconds_remaining = 18.0
    d_step1 = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_step1.should_bid is True
    assert d_step1.bid_amount_eur == 330
    assert d_step1.is_reserve_step is True

    # Reserve still not met at 330 €, next step 360 € -> raises one more step
    lot.my_last_bid_eur = 330
    lot.current_bid_eur = 330
    lot.next_min_bid_eur = 360
    lot.seconds_remaining = 14.0
    d_step2 = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_step2.should_bid is True
    assert d_step2.bid_amount_eur == 360
    assert d_step2.is_reserve_step is True

    # At 360 € the reserve notice disappears (ReserveStatus.MET) -> stops self-bidding immediately
    lot.my_last_bid_eur = 360
    lot.current_bid_eur = 360
    lot.next_min_bid_eur = 390
    lot.reserve_status = ReserveStatus.MET
    d_unlocked = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_unlocked.should_bid is False

    # Edge case: reserve is still not met and next step (460 €) exceeds ceiling (450 €)
    lot.reserve_status = ReserveStatus.NOT_MET
    lot.current_bid_eur = 430
    lot.next_min_bid_eur = 460
    d_cap = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_cap.should_bid is False


async def test_process_tick_and_decide_releases_and_reserves_atomically() -> None:
    ledger = BudgetLedger(global_budget_eur=500)
    optimizer = PortfolioOptimizer()
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)

    lot = WatchLot(
        lot_id="lot-atomic",
        url="https://www.catawiki.com/es/l/3003-tissot",
        max_bid_eur=400,
        current_bid_eur=200,
        next_min_bid_eur=220,
        seconds_remaining=15.0,
        is_leader=False,
    )

    # Tick 1: decides to bid 220 € and commits them to the Ledger
    d1 = await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert d1.should_bid is True
    assert d1.bid_amount_eur == 220
    assert ledger.committed_leader_eur == 220
    lot.record_own_bid(220, reserve_status=ReserveStatus.NO_RESERVE)

    # Tick 2: a rival outbids us, placing lot at 410 € (> max_bid_eur 400 €)
    lot.apply_market_snapshot(
        current_bid_eur=390,
        next_min_bid_eur=410,
        seconds_remaining=85.0,
        is_leader=False,
    )
    d2 = await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert d2.should_bid is False
    # The committed amount of 220 € should have been automatically released
    assert ledger.committed_leader_eur == 0
    assert ledger.available_eur == 500


async def test_process_tick_and_decide_syncs_preexisting_leader_commitment() -> None:
    ledger = BudgetLedger(global_budget_eur=1500)
    optimizer = PortfolioOptimizer()
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)

    lot = WatchLot(
        lot_id="lot-13",
        url="https://www.catawiki.com/es/l/1313-orient",
        max_bid_eur=200,
    )
    lot.apply_market_snapshot(
        current_bid_eur=1,
        next_min_bid_eur=2,
        seconds_remaining=594459.0,
        is_leader=True,
        reserve_status=ReserveStatus.NO_RESERVE,
    )

    decision = await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert decision.should_bid is False
    assert lot.status == AuctionStatus.LEADER
    assert lot.my_last_bid_eur == 1
    assert ledger.committed_leader_eur == 1
    assert ledger.available_eur == 1499



async def test_row12_regression_final_consolidation_keeps_generic_loss() -> None:
    """
    Reproduces the 2026-10-19 row 12 sequence: lot closes in assault without any budget
    block, the optimizer zeroes its dynamic ceiling, and the final consolidation
    reconciles it again. The outcome must remain PERDIDO (never Sin Presupuesto).
    """
    ledger = BudgetLedger(global_budget_eur=1500)
    optimizer = PortfolioOptimizer()
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)
    lot = WatchLot(
        lot_id="lot-row12",
        url="https://www.catawiki.com/es/l/107567796-seiko-presage-srpg05j1",
        max_bid_eur=350,
    )

    # Closing tick inside the assault loop
    lot.apply_market_snapshot(
        current_bid_eur=320,
        next_min_bid_eur=340,
        seconds_remaining=0.0,
        is_leader=False,
        reserve_status=ReserveStatus.NO_RESERVE,
        is_closed=True,
    )
    await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert lot.status == AuctionStatus.LOST_OTHER

    # _emit_and_enqueue -> refresh_all_ceilings zeroes the ceiling of the closed lot
    optimizer.refresh_all_ceilings([lot], ledger)
    assert lot.dynamic_ceiling_eur == 0

    # Final consolidation after the loop
    await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert lot.status == AuctionStatus.LOST_OTHER
    assert "Sin Presupuesto" not in lot.status.value


async def test_genuine_budget_block_in_snipe_window_closes_as_no_budget() -> None:
    ledger = BudgetLedger(global_budget_eur=300)
    optimizer = PortfolioOptimizer()
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)
    lot = WatchLot(
        lot_id="lot-budget",
        url="https://www.catawiki.com/es/l/4004-longines",
        max_bid_eur=400,
    )
    lot.apply_market_snapshot(
        current_bid_eur=310,
        next_min_bid_eur=330,
        seconds_remaining=15.0,
        is_leader=False,
        reserve_status=ReserveStatus.NO_RESERVE,
    )
    decision = await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert decision.should_bid is False
    assert lot.budget_blocked_at == 15.0
    assert "techo dinámico/presupuesto" in lot.last_event

    lot.apply_market_snapshot(
        current_bid_eur=310,
        next_min_bid_eur=330,
        seconds_remaining=0.0,
        is_leader=False,
        reserve_status=ReserveStatus.NO_RESERVE,
        is_closed=True,
    )
    await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert lot.status == AuctionStatus.LOST_NO_BUDGET


async def test_budget_block_outside_window_is_not_recorded() -> None:
    ledger = BudgetLedger(global_budget_eur=300)
    optimizer = PortfolioOptimizer()
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)
    lot = WatchLot(
        lot_id="lot-budget-early",
        url="https://www.catawiki.com/es/l/4005-longines",
        max_bid_eur=400,
    )
    lot.apply_market_snapshot(
        current_bid_eur=310,
        next_min_bid_eur=330,
        seconds_remaining=200.0,
        is_leader=False,
        reserve_status=ReserveStatus.NO_RESERVE,
    )
    await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert lot.budget_blocked_at is None


async def test_authorized_bid_clears_previous_budget_block() -> None:
    ledger = BudgetLedger(global_budget_eur=500)
    optimizer = PortfolioOptimizer()
    engine = TacticalBiddingEngine(snipe_window_seconds=25.0)
    lot = WatchLot(
        lot_id="lot-recover",
        url="https://www.catawiki.com/es/l/4006-tissot",
        max_bid_eur=400,
        current_bid_eur=200,
        next_min_bid_eur=220,
        seconds_remaining=15.0,
    )
    lot.budget_blocked_at = 20.0
    decision = await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert decision.should_bid is True
    assert lot.budget_blocked_at is None
