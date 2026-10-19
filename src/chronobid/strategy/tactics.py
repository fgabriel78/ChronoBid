"""Tactical decision engine in final seconds, overtime extensions (+90s), and reserve ladder."""

from __future__ import annotations

from collections.abc import Iterable

from chronobid.models.auction import (
    AuctionStatus,
    BidDecision,
    ReserveStatus,
    WatchLot,
)
from chronobid.strategy.ledger import BudgetLedger
from chronobid.strategy.optimizer import PortfolioOptimizer


class TacticalBiddingEngine:
    """
    Evaluates real-time state of a lot and decides whether to submit
    the next minimum bid step respecting:
      1. Tactical invisibility until `< snipe_window_seconds` (25s by default).
      2. Strictly bidding the next minimum increment on Catawiki.
      3. Counter-attacking in overtime extension loops (+90s) if a rival outbids us.
      4. Step-by-step ladder to unlock hidden reserve price when already
         leading in the final 25s but unmet reserve notice persists.
    """

    def __init__(
        self,
        *,
        snipe_window_seconds: float = 25.0,
        overtime_window_seconds: float = 95.0,
        counter_in_overtime: bool = True,
    ) -> None:
        if snipe_window_seconds <= 0:
            raise ValueError("snipe_window_seconds debe ser mayor que 0.")
        self.snipe_window_seconds = snipe_window_seconds
        self.overtime_window_seconds = overtime_window_seconds
        self.counter_in_overtime = counter_in_overtime

    def evaluate_lot(
        self,
        lot: WatchLot,
        dynamic_ceiling_eur: int | None = None,
    ) -> BidDecision:
        """
        Purely evaluates what tactical action to take for `lot`
        given its `dynamic_ceiling_eur`.
        """
        raw_ceiling = (
            dynamic_ceiling_eur
            if dynamic_ceiling_eur is not None
            else lot.effective_ceiling_eur
        )
        effective_ceiling = min(lot.max_bid_eur, max(0, raw_ceiling))
        lot.dynamic_ceiling_eur = effective_ceiling

        if lot.is_closed or lot.status.is_terminal:
            return BidDecision(
                should_bid=False,
                reason="Subasta cerrada o en estado terminal",
                dynamic_ceiling_eur=effective_ceiling,
            )

        if lot.seconds_remaining is None:
            return BidDecision(
                should_bid=False,
                reason="Cuenta atrás aún no disponible",
                dynamic_ceiling_eur=effective_ceiling,
            )

        next_min = lot.next_min_bid_eur
        if next_min <= 0:
            return BidDecision(
                should_bid=False,
                reason="Siguiente incremento mínimo no disponible",
                dynamic_ceiling_eur=effective_ceiling,
            )

        # Case 1: User is NOT the current highest bidder
        if not lot.is_leader:
            if next_min > lot.max_bid_eur:
                lot.status = AuctionStatus.DISCARDED_MAX_EXCEEDED
                lot.notes = f"Superó puja máxima ({next_min} € > {lot.max_bid_eur} €)"
                return BidDecision(
                    should_bid=False,
                    reason=lot.notes,
                    dynamic_ceiling_eur=effective_ceiling,
                )

            in_snipe_window = lot.seconds_remaining <= self.snipe_window_seconds
            in_overtime_counter = (
                self.counter_in_overtime
                and lot.my_last_bid_eur > 0
                and lot.seconds_remaining <= self.overtime_window_seconds
            )

            if not (in_snipe_window or in_overtime_counter):
                return BidDecision(
                    should_bid=False,
                    reason=(
                        f"Invisibilidad previa activa: {lot.seconds_remaining:.1f}s "
                        f"> {self.snipe_window_seconds:.0f}s"
                    ),
                    dynamic_ceiling_eur=effective_ceiling,
                )

            if next_min > effective_ceiling:
                # A bid was due (window open, next_min <= max) but ceiling/budget prevents it.
                lot.budget_blocked_at = lot.seconds_remaining
                lot.record_event(
                    f"Bloqueado por techo dinámico/presupuesto ({next_min} € > {effective_ceiling} €)"
                )
                return BidDecision(
                    should_bid=False,
                    reason=lot.notes,
                    dynamic_ceiling_eur=effective_ceiling,
                )

            reason = (
                f"Contraataque en prórroga (+90s) con puja mínima de {next_min} €"
                if (in_overtime_counter and not in_snipe_window)
                else f"Entrada táctica (< {self.snipe_window_seconds:.0f}s) con puja mínima de {next_min} €"
            )
            return BidDecision(
                should_bid=True,
                bid_amount_eur=next_min,
                reason=reason,
                is_reserve_step=False,
                dynamic_ceiling_eur=effective_ceiling,
            )

        # Case 2: User is ALREADY the provisional leader
        if lot.reserve_status != ReserveStatus.NOT_MET:
            return BidDecision(
                should_bid=False,
                reason="Líder provisional con precio de reserva alcanzado o inexistente",
                dynamic_ceiling_eur=effective_ceiling,
            )

        # Is leader but reserve price has NOT been met -> Ladder in last 25s
        if lot.seconds_remaining > self.snipe_window_seconds:
            return BidDecision(
                should_bid=False,
                reason=(
                    f"Líder con reserva no alcanzada; esperando a < {self.snipe_window_seconds:.0f}s "
                    f"(restan {lot.seconds_remaining:.1f}s)"
                ),
                dynamic_ceiling_eur=effective_ceiling,
            )

        if next_min <= lot.current_bid_eur:
            return BidDecision(
                should_bid=False,
                reason="Escalón siguiente no superior a la puja actual",
                dynamic_ceiling_eur=effective_ceiling,
            )

        if next_min > lot.max_bid_eur or next_min > effective_ceiling:
            lot.notes = (
                f"Escalera de reserva detenida: {next_min} € supera techo ({effective_ceiling} €)"
            )
            return BidDecision(
                should_bid=False,
                reason=lot.notes,
                dynamic_ceiling_eur=effective_ceiling,
            )

        return BidDecision(
            should_bid=True,
            bid_amount_eur=next_min,
            reason=f"Escalera de precio de reserva: subiendo un escalón a {next_min} €",
            is_reserve_step=True,
            dynamic_ceiling_eur=effective_ceiling,
        )

    async def reconcile_lot_with_ledger(
        self,
        lot: WatchLot,
        ledger: BudgetLedger,
    ) -> None:
        """
        Atomically reconciles accounting state of `lot` in `BudgetLedger`
        after applying a market snapshot (both in Patrol Mode and Assault Mode):
          - If the lot closed as WON, consolidates firm expenditure.
          - If the lot closed without being won or reached terminal status, releases any commitment.
          - If the lot remains open and `lot.is_leader` is True, synchronizes `lot.current_bid_eur`
            as leader committed balance.
          - If the lot remains open and `lot.is_leader` is False, releases any prior commitment.

        The status of a terminal lot is never changed here: the final outcome is classified
        once by `WatchLot.apply_market_snapshot` at the closing transition, so repeated
        reconciliations (e.g. assault final consolidation) cannot re-label it.
        """
        if lot.is_closed or lot.status.is_terminal:
            if lot.status == AuctionStatus.WON:
                if ledger.get_won(lot.lot_id) == 0:
                    await ledger.confirm_won(lot.lot_id, final_amount_eur=lot.current_bid_eur)
            elif ledger.get_committed(lot.lot_id) > 0:
                await ledger.release(lot.lot_id)
            return

        if lot.is_leader:
            await ledger.sync_leader_commitment(lot.lot_id, lot.current_bid_eur)
        elif ledger.get_committed(lot.lot_id) > 0:
            await ledger.release(lot.lot_id)

    async def process_tick_and_decide(
        self,
        lot: WatchLot,
        all_lots: Iterable[WatchLot],
        ledger: BudgetLedger,
        optimizer: PortfolioOptimizer,
    ) -> BidDecision:
        """
        Processes a full Assault Mode tick for `lot`:
          1. If the lot has closed, consolidates WON or releases balance if not awarded.
          2. Synchronizes commitment if user is leader or instantly releases if outbid.
          3. Computes Dynamic Ceiling E_i using `PortfolioOptimizer`.
          4. Evaluates tactical decision; if bidding is due, attempts to reserve atomic
             balance in `BudgetLedger` before authorizing bid submission.
        """
        all_lots_list = list(all_lots)

        # 1 and 2. Reconcile closing state or current leadership with BudgetLedger
        await self.reconcile_lot_with_ledger(lot, ledger)

        if lot.is_closed or lot.status.is_terminal:
            return BidDecision(
                should_bid=False,
                reason="Subasta finalizada",
                dynamic_ceiling_eur=lot.dynamic_ceiling_eur,
            )

        # 3. Compute Dynamic Ceiling E_i
        ceiling = optimizer.compute_dynamic_ceiling(lot, all_lots_list, ledger)

        # 4. Evaluate tactics
        decision = self.evaluate_lot(lot, dynamic_ceiling_eur=ceiling)
        if not decision.should_bid or decision.bid_amount_eur is None:
            return decision

        # 5. Atomically reserve balance before allowing bid submission in UI
        reserved = await ledger.try_reserve(
            lot.lot_id,
            decision.bid_amount_eur,
            max_lot_bid_eur=ceiling,
        )
        if not reserved:
            lot.budget_blocked_at = lot.seconds_remaining
            lot.record_event(f"Sin saldo disponible para pujar {decision.bid_amount_eur} €")
            return BidDecision(
                should_bid=False,
                reason=lot.notes,
                dynamic_ceiling_eur=ceiling,
            )

        # A bid is now authorized: any earlier budget block has been recovered.
        lot.budget_blocked_at = None
        return decision
