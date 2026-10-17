"""Tests unitarios de los modelos de dominio en clock_agent.models.auction."""

import pytest

from clock_agent.models.auction import (
    AuctionStatus,
    BidDecision,
    GlobalBudgetState,
    ReserveStatus,
    WatchLot,
    extract_lot_id,
)


def test_watch_lot_valid_creation() -> None:
    lot = WatchLot(
        lot_id="",
        url="https://www.catawiki.com/es/l/98765432-omega-seamaster",
        max_bid_eur=450,
        row_index=7,
    )
    assert lot.lot_id == "lot-98765432"
    assert lot.status == AuctionStatus.PENDING
    assert lot.dynamic_ceiling_eur == 450
    assert lot.is_viable is True
    assert lot.can_bid_amount(450) is True
    assert lot.can_bid_amount(451) is False


@pytest.mark.parametrize(
    ("url", "max_bid"),
    [
        ("", 300),
        ("not-a-url", 300),
        ("https://www.catawiki.com/es/l/123456", 0),
        ("https://www.catawiki.com/es/l/123456", -50),
    ],
)
def test_watch_lot_invalid_inputs_marked_as_validation_error(url: str, max_bid: int) -> None:
    lot = WatchLot(lot_id="test", url=url, max_bid_eur=max_bid)
    assert lot.status == AuctionStatus.VALIDATION_ERROR
    assert lot.status.is_terminal is True
    assert lot.is_viable is False
    assert lot.can_bid_amount(100) is False


def test_clean_bid_invariant_and_dynamic_ceiling() -> None:
    lot = WatchLot(
        lot_id="lot-1",
        url="https://www.catawiki.com/es/l/111111-seiko",
        max_bid_eur=500,
        dynamic_ceiling_eur=380,
    )
    assert lot.effective_ceiling_eur == 380
    assert lot.can_bid_amount(380) is True
    assert lot.can_bid_amount(390) is False

    lot.record_own_bid(350, reserve_status=ReserveStatus.NOT_MET)
    assert lot.my_last_bid_eur == 350
    assert lot.is_leader is True
    assert lot.status == AuctionStatus.LEADER

    with pytest.raises(ValueError, match="Puja inválida"):
        lot.record_own_bid(400)


def test_auction_transitions_patrol_assault_leader_outbid_and_pruning() -> None:
    lot = WatchLot(
        lot_id="lot-2",
        url="https://www.catawiki.com/es/l/222222-tissot",
        max_bid_eur=300,
    )

    # Modo Patrulla (> 5 min)
    status = lot.apply_market_snapshot(
        current_bid_eur=120,
        next_min_bid_eur=130,
        seconds_remaining=600,
        is_leader=False,
        reserve_status=ReserveStatus.NOT_MET,
    )
    assert status == AuctionStatus.PATROL
    assert lot.is_viable is True

    # Transición a Modo Asalto (<= 300s)
    status = lot.apply_market_snapshot(
        current_bid_eur=150,
        next_min_bid_eur=160,
        seconds_remaining=240,
        is_leader=False,
    )
    assert status == AuctionStatus.ASSAULT

    # Pasa a Líder
    lot.record_own_bid(160)
    status = lot.apply_market_snapshot(
        current_bid_eur=160,
        next_min_bid_eur=170,
        seconds_remaining=85,
        is_leader=True,
        reserve_status=ReserveStatus.MET,
    )
    assert status == AuctionStatus.LEADER

    # Rival supera la puja pero aún dentro de máximo
    status = lot.apply_market_snapshot(
        current_bid_eur=180,
        next_min_bid_eur=190,
        seconds_remaining=89,
        is_leader=False,
    )
    assert status == AuctionStatus.OUTBID
    assert lot.is_viable is True

    # Rival supera el máximo -> poda automática (PERDIDO: Superó Máximo)
    status = lot.apply_market_snapshot(
        current_bid_eur=300,
        next_min_bid_eur=320,
        seconds_remaining=75,
        is_leader=False,
    )
    assert status == AuctionStatus.DISCARDED_MAX_EXCEEDED
    assert lot.status.is_terminal is True
    assert lot.is_viable is False


def test_auction_closing_outcomes_won_and_reserve_not_met() -> None:
    lot_won = WatchLot(
        lot_id="lot-won",
        url="https://www.catawiki.com/es/l/333333-longines",
        max_bid_eur=600,
    )
    lot_won.apply_market_snapshot(
        current_bid_eur=480,
        next_min_bid_eur=500,
        seconds_remaining=0,
        is_leader=True,
        reserve_status=ReserveStatus.MET,
        is_closed=True,
    )
    assert lot_won.status == AuctionStatus.WON
    assert lot_won.status.is_terminal is True

    lot_no_reserve = WatchLot(
        lot_id="lot-reserve",
        url="https://www.catawiki.com/es/l/444444-zenith",
        max_bid_eur=600,
    )
    lot_no_reserve.apply_market_snapshot(
        current_bid_eur=550,
        next_min_bid_eur=580,
        seconds_remaining=0,
        is_leader=True,
        reserve_status=ReserveStatus.NOT_MET,
        is_closed=True,
    )
    assert lot_no_reserve.status == AuctionStatus.NOT_AWARDED_RESERVE
    assert lot_no_reserve.status.is_terminal is True


def test_global_budget_state_invariants() -> None:
    state = GlobalBudgetState(global_budget_eur=1000, spent_eur=350, committed_leader_eur=250)
    assert state.available_eur == 400
    assert state.can_afford_delta(400) is True
    assert state.can_afford_delta(401) is False

    with pytest.raises(ValueError, match="Invariante de presupuesto violado"):
        GlobalBudgetState(global_budget_eur=1000, spent_eur=700, committed_leader_eur=400)


def test_bid_decision_validation() -> None:
    decision_ok = BidDecision(
        should_bid=True,
        bid_amount_eur=220,
        reason="Ataque en últimos 25s",
        dynamic_ceiling_eur=250,
    )
    assert decision_ok.should_bid is True
    assert decision_ok.bid_amount_eur == 220

    with pytest.raises(ValueError, match="importe entero positivo"):
        BidDecision(should_bid=True, bid_amount_eur=0)

    with pytest.raises(ValueError, match="techo dinámico"):
        BidDecision(should_bid=True, bid_amount_eur=300, dynamic_ceiling_eur=250)


def test_extract_lot_id() -> None:
    assert (
        extract_lot_id("https://www.catawiki.com/es/l/87654321-rolex-datejust")
        == "lot-87654321"
    )
    assert extract_lot_id("", fallback_row=9) == "row-9"
