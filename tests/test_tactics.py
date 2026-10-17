"""Tests unitarios del motor táctico en tests/test_tactics.py."""

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
        seconds_remaining=88.0,  # Prórroga de +90s recién activada por un rival
        is_leader=False,
    )

    # Contraataque dentro del techo dinámico
    decision_counter = engine.evaluate_lot(lot, dynamic_ceiling_eur=350)
    assert decision_counter.should_bid is True
    assert decision_counter.bid_amount_eur == 260
    assert "prórroga" in decision_counter.reason

    # El rival sube por encima del techo dinámico (360 > 350) -> abandono inmediato
    lot.current_bid_eur = 340
    lot.next_min_bid_eur = 360
    decision_abandon = engine.evaluate_lot(lot, dynamic_ceiling_eur=350)
    assert decision_abandon.should_bid is False

    # El rival supera la puja máxima del reloj (420 > 400) -> poda
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

    # Faltando 60s (> 25s), espera sin autopujar todavía
    d_wait = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_wait.should_bid is False

    # Entra en los últimos 25s con reserva NO alcanzada -> sube un único escalón a 330 €
    lot.seconds_remaining = 18.0
    d_step1 = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_step1.should_bid is True
    assert d_step1.bid_amount_eur == 330
    assert d_step1.is_reserve_step is True

    # Sigue sin alcanzarse la reserva en 330 €, siguiente escalón 360 € -> sube un escalón más
    lot.my_last_bid_eur = 330
    lot.current_bid_eur = 330
    lot.next_min_bid_eur = 360
    lot.seconds_remaining = 14.0
    d_step2 = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_step2.should_bid is True
    assert d_step2.bid_amount_eur == 360
    assert d_step2.is_reserve_step is True

    # En 360 € desaparece el aviso de reserva (ReserveStatus.MET) -> detiene autopujas al instante
    lot.my_last_bid_eur = 360
    lot.current_bid_eur = 360
    lot.next_min_bid_eur = 390
    lot.reserve_status = ReserveStatus.MET
    d_unlocked = engine.evaluate_lot(lot, dynamic_ceiling_eur=450)
    assert d_unlocked.should_bid is False

    # Caso borde: la reserva aún no se alcanza y el siguiente escalón (460 €) supera el techo (450 €)
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

    # Tick 1: decide pujar 220 € y los compromete en el Ledger
    d1 = await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert d1.should_bid is True
    assert d1.bid_amount_eur == 220
    assert ledger.committed_leader_eur == 220
    lot.record_own_bid(220, reserve_status=ReserveStatus.NO_RESERVE)

    # Tick 2: un rival nos supera y pone el lote en 410 € (> max_bid_eur 400 €)
    lot.apply_market_snapshot(
        current_bid_eur=390,
        next_min_bid_eur=410,
        seconds_remaining=85.0,
        is_leader=False,
    )
    d2 = await engine.process_tick_and_decide(lot, [lot], ledger, optimizer)
    assert d2.should_bid is False
    # El saldo comprometido de 220 € ha debido liberarse automáticamente
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

