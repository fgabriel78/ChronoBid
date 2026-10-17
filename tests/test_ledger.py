"""Tests de contabilidad atómica y concurrencia para BudgetLedger."""

import asyncio

import pytest

from chronobid.strategy.ledger import BudgetLedger


async def test_ledger_reserve_differential_release_and_confirm_won() -> None:
    ledger = BudgetLedger(global_budget_eur=1000)
    assert ledger.available_eur == 1000

    # Reserva inicial como líder en lot-1 por 300 €
    ok = await ledger.try_reserve("lot-1", 300, max_lot_bid_eur=400)
    assert ok is True
    assert ledger.committed_leader_eur == 300
    assert ledger.available_eur == 700

    # Escalera de reserva en lot-1: sube de 300 € a 340 € (descuenta solo +40 € diferenciales)
    ok = await ledger.try_reserve("lot-1", 340, max_lot_bid_eur=400)
    assert ok is True
    assert ledger.committed_leader_eur == 340
    assert ledger.available_eur == 660
    assert ledger.max_purchasing_power_for_lot("lot-1") == 1000

    # Intento de superar el máximo individual del lote (420 > 400) -> rechazado
    ok_exceed = await ledger.try_reserve("lot-1", 420, max_lot_bid_eur=400)
    assert ok_exceed is False
    assert ledger.committed_leader_eur == 340

    # Reserva simultánea en lot-2 por 500 €
    ok2 = await ledger.try_reserve("lot-2", 500, max_lot_bid_eur=600)
    assert ok2 is True
    assert ledger.committed_leader_eur == 840
    assert ledger.available_eur == 160

    # Otro postor supera nuestra puja en lot-1 -> liberación inmediata de 340 €
    released = await ledger.release("lot-1")
    assert released == 340
    assert ledger.committed_leader_eur == 500
    assert ledger.available_eur == 500

    # lot-2 finaliza como GANADO
    won_amt = await ledger.confirm_won("lot-2")
    assert won_amt == 500
    assert ledger.spent_eur == 500
    assert ledger.committed_leader_eur == 0
    assert ledger.available_eur == 500


async def test_ledger_blocks_overlapping_bids_exceeding_global_budget() -> None:
    ledger = BudgetLedger(global_budget_eur=600)

    # Reloj 1 compromete 350 €
    assert await ledger.try_reserve("lot-1", 350, max_lot_bid_eur=400) is True
    assert ledger.available_eur == 250

    # Reloj 2 intenta comprometer 280 € en cierre simultáneo -> bloqueado
    assert await ledger.try_reserve("lot-2", 280, max_lot_bid_eur=350) is False
    assert ledger.committed_leader_eur == 350
    assert ledger.available_eur == 250


async def test_ledger_high_concurrency_never_exceeds_global_budget() -> None:
    """
    20 tareas de subasta compiten simultáneamente por un presupuesto de 1000 €
    con pujas de 150 € cada una. A lo sumo 6 tareas (900 €) pueden obtener reserva.
    """
    ledger = BudgetLedger(global_budget_eur=1000)
    start_gate = asyncio.Event()

    async def bidder(idx: int) -> bool:
        await start_gate.wait()
        return await ledger.try_reserve(f"lot-{idx}", 150, max_lot_bid_eur=200)

    tasks = [asyncio.create_task(bidder(i)) for i in range(20)]
    start_gate.set()
    results = await asyncio.gather(*tasks)

    winners = sum(1 for r in results if r)
    assert winners == 6
    assert ledger.committed_leader_eur == 900
    assert ledger.available_eur == 100
    snap = ledger.snapshot()
    assert snap.spent_eur + snap.committed_leader_eur <= snap.global_budget_eur


async def test_ledger_update_global_budget_validation() -> None:
    ledger = BudgetLedger(global_budget_eur=500)
    await ledger.try_reserve("lot-1", 300)

    snap = await ledger.update_global_budget(800)
    assert snap.global_budget_eur == 800
    assert snap.available_eur == 500

    with pytest.raises(ValueError, match="menor que la suma"):
        await ledger.update_global_budget(200)


async def test_ledger_sync_leader_commitment_manual_bid_and_release() -> None:
    ledger = BudgetLedger(global_budget_eur=1500)

    # Sincronización de puja manual previa de 1 € detectada en Modo Patrulla
    synced = await ledger.sync_leader_commitment("lot-13", 1)
    assert synced == 1
    assert ledger.committed_leader_eur == 1
    assert ledger.available_eur == 1499
    assert ledger.max_purchasing_power_for_lot("lot-13") == 1500

    # Actualización del compromiso en el mismo lote a 25 €
    synced_up = await ledger.sync_leader_commitment("lot-13", 25)
    assert synced_up == 25
    assert ledger.committed_leader_eur == 25
    assert ledger.available_eur == 1475

    # Si otra puja manual externa excediera el saldo libre restante, se acota sin romper invariante
    synced_capped = await ledger.sync_leader_commitment("lot-99", 2000)
    assert synced_capped == 1475
    snap = ledger.snapshot()
    assert snap.committed_leader_eur == 1500
    assert snap.available_eur == 0

    # Liberación con importe 0 o mediante release()
    assert await ledger.sync_leader_commitment("lot-99", 0) == 0
    assert await ledger.release("lot-13") == 25
    assert ledger.committed_leader_eur == 0
    assert ledger.available_eur == 1500

