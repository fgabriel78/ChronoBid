"""Motor de decisión táctica en los segundos finales, prórrogas (+90s) y escalera de reserva."""

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
    Evalúa el estado en tiempo real de un lote y decide si se debe enviar
    el siguiente escalón mínimo de puja respetando:
      1. Invisibilidad táctica hasta `< snipe_window_seconds` (25s por defecto).
      2. Puja estrictamente por el siguiente incremento mínimo de Catawiki.
      3. Contraataque en bucles de prórroga (+90s) si un rival supera nuestra puja.
      4. Escalera paso a paso para desbloquear el precio de reserva oculto cuando ya
         somos líderes en los últimos 25s pero persiste el aviso de reserva no alcanzada.
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
        Evalúa de forma pura qué acción táctica corresponde tomar para `lot`
        dado su `dynamic_ceiling_eur`.
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

        # Caso 1: El usuario NO es el máximo pujador actual
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
                lot.notes = (
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

        # Caso 2: El usuario YA es el líder provisional
        if lot.reserve_status != ReserveStatus.NOT_MET:
            return BidDecision(
                should_bid=False,
                reason="Líder provisional con precio de reserva alcanzado o inexistente",
                dynamic_ceiling_eur=effective_ceiling,
            )

        # Es líder pero el precio de reserva NO se ha alcanzado -> Escalera en últimos 25s
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
        Reconcilia de forma atómica el estado contable de `lot` en `BudgetLedger`
        tras aplicar un snapshot de mercado (tanto en Modo Patrulla como en Modo Asalto):
          - Si el lote cerró como GANADO, consolida el gasto en firme.
          - Si el lote cerró sin ganarse o quedó en estado terminal, libera cualquier compromiso.
          - Si el lote sigue abierto y `lot.is_leader` es True, sincroniza `lot.current_bid_eur`
            como saldo comprometido de líder.
          - Si el lote sigue abierto y `lot.is_leader` es False, libera cualquier compromiso previo.
        """
        if lot.is_closed or lot.status.is_terminal:
            if lot.status == AuctionStatus.WON:
                if ledger.get_won(lot.lot_id) == 0:
                    await ledger.confirm_won(lot.lot_id, final_amount_eur=lot.current_bid_eur)
            else:
                if ledger.get_committed(lot.lot_id) > 0:
                    await ledger.release(lot.lot_id)
                if (
                    lot.is_closed
                    and not lot.is_leader
                    and lot.status
                    not in {
                        AuctionStatus.DISCARDED_MAX_EXCEEDED,
                        AuctionStatus.VALIDATION_ERROR,
                    }
                    and lot.next_min_bid_eur <= lot.max_bid_eur
                    and (
                        ledger.available_eur < lot.next_min_bid_eur
                        or lot.dynamic_ceiling_eur < lot.next_min_bid_eur
                    )
                ):
                    lot.status = AuctionStatus.LOST_NO_BUDGET
                    lot.notes = "Subasta cerrada sin presupuesto/techo disponible suficiente"
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
        Procesa un tick completo de Modo Asalto para `lot`:
          1. Si el lote ha cerrado, consolida GANADO o libera saldo si no fue adjudicado.
          2. Sincroniza el compromiso si el usuario es líder o lo libera al instante si fue superado.
          3. Calcula el Techo Dinámico E_i con `PortfolioOptimizer`.
          4. Evalúa la decisión táctica; si procede pujar, intenta reservar el saldo
             atómico en `BudgetLedger` antes de autorizar el envío de la puja.
        """
        all_lots_list = list(all_lots)

        # 1 y 2. Reconciliar estado de cierre o liderazgo actual con BudgetLedger
        await self.reconcile_lot_with_ledger(lot, ledger)

        if lot.is_closed or lot.status.is_terminal:
            return BidDecision(
                should_bid=False,
                reason="Subasta finalizada",
                dynamic_ceiling_eur=lot.dynamic_ceiling_eur,
            )

        # 3. Calcular Techo Dinámico E_i
        ceiling = optimizer.compute_dynamic_ceiling(lot, all_lots_list, ledger)

        # 4. Evaluar táctica
        decision = self.evaluate_lot(lot, dynamic_ceiling_eur=ceiling)
        if not decision.should_bid or decision.bid_amount_eur is None:
            return decision

        # 5. Reservar saldo de forma atómica antes de permitir la puja en UI
        reserved = await ledger.try_reserve(
            lot.lot_id,
            decision.bid_amount_eur,
            max_lot_bid_eur=ceiling,
        )
        if not reserved:
            lot.notes = f"Sin saldo disponible para pujar {decision.bid_amount_eur} €"
            return BidDecision(
                should_bid=False,
                reason=lot.notes,
                dynamic_ceiling_eur=ceiling,
            )

        return decision
