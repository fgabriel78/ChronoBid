"""Dynamic portfolio optimizer (PortfolioOptimizer) to maximize the number of watches."""

from __future__ import annotations

from collections.abc import Iterable

from chronobid.models.auction import AuctionStatus, WatchLot
from chronobid.strategy.ledger import BudgetLedger


class PortfolioOptimizer:
    """
    Computes real-time pruning of overpriced lots and Dynamic Ceiling (E_i)
    for each active auction to maximize the total number of awarded watches.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.30,
        min_slack_ratio: float = 0.15,
    ) -> None:
        if not (0.0 <= alpha <= 1.0):
            raise ValueError("El factor alpha debe estar entre 0.0 y 1.0.")
        if not (0.0 <= min_slack_ratio <= 1.0):
            raise ValueError("El umbral de holgura mínima H_min debe estar entre 0.0 y 1.0.")
        self.alpha = alpha
        self.min_slack_ratio = min_slack_ratio

    def prune_lots(self, lots: Iterable[WatchLot]) -> list[WatchLot]:
        """
        Automatically discards (PERDIDO: Superó Máximo) any active lot where the user
        is not leader and the next minimum step exceeds the clean maximum bid from Excel.
        Returns the list of lots that were just pruned.
        """
        pruned: list[WatchLot] = []
        for lot in lots:
            if lot.status.is_terminal or lot.is_closed:
                continue
            if not lot.is_leader and lot.next_min_bid_eur > lot.max_bid_eur:
                lot.status = AuctionStatus.DISCARDED_MAX_EXCEEDED
                lot.dynamic_ceiling_eur = 0
                lot.notes = (
                    f"Descartado: siguiente mínimo ({lot.next_min_bid_eur} €) > "
                    f"puja máxima ({lot.max_bid_eur} €)"
                )
                pruned.append(lot)
        return pruned

    def estimate_lot_cost(self, lot: WatchLot) -> int:
        """
        Computes conservative expected cost for a viable future lot:
            C_hat_j = NextMinimum_j + alpha * (M_j - NextMinimum_j)
        """
        next_min = lot.next_min_bid_eur if lot.next_min_bid_eur > 0 else max(1, lot.current_bid_eur)
        if next_min > lot.max_bid_eur:
            return lot.max_bid_eur
        estimated = next_min + self.alpha * (lot.max_bid_eur - next_min)
        return max(1, int(round(estimated)))

    def compute_slack_ratio(self, lot: WatchLot) -> float:
        """
        Computes the viability index (slack ratio H_j) of a lot:
            H_j = (M_j - NextMinimum_j) / M_j
        """
        if lot.max_bid_eur <= 0:
            return 0.0
        next_min = lot.next_min_bid_eur if lot.next_min_bid_eur > 0 else max(1, lot.current_bid_eur)
        if next_min > lot.max_bid_eur:
            return -1.0
        return (lot.max_bid_eur - next_min) / float(lot.max_bid_eur)

    def compute_portfolio_reserve(
        self,
        target_lot: WatchLot,
        all_lots: Iterable[WatchLot],
        purchasing_power_eur: int,
    ) -> int:
        """
        Computes capital to reserve (PortfolioReserve_i) when there is a combination
        of 2 or more future watches with high slack (H_j >= H_min) whose combined
        estimated cost fits within available purchasing power.
        """
        if purchasing_power_eur <= 0:
            return 0

        candidate_costs: list[int] = []
        for lot in all_lots:
            if lot.lot_id == target_lot.lot_id:
                continue
            if not lot.is_viable:
                continue
            # Lots where we are already leaders already have their balance deducted in BudgetLedger
            if lot.is_leader:
                continue

            slack = self.compute_slack_ratio(lot)
            if slack >= self.min_slack_ratio:
                cost = self.estimate_lot_cost(lot)
                if 0 < cost < target_lot.max_bid_eur and cost <= purchasing_power_eur:
                    candidate_costs.append(cost)

        if len(candidate_costs) < 2:
            return 0

        candidate_costs.sort()

        # Find the largest combination of m >= 2 future watches that fits in purchasing_power_eur
        accumulated = 0
        count = 0
        for cost in candidate_costs:
            if accumulated + cost <= purchasing_power_eur:
                accumulated += cost
                count += 1
            else:
                break

        if count < 2:
            return 0

        return accumulated

    def compute_dynamic_ceiling(
        self,
        target_lot: WatchLot,
        all_lots: Iterable[WatchLot],
        ledger: BudgetLedger,
    ) -> int:
        """
        Computes and updates the Effective Dynamic Ceiling E_i of lot `target_lot`:
            E_i = min(M_i, MyCurrentBid_i + B_avail - PortfolioReserve_i)
        """
        all_lots_list = list(all_lots)
        self.prune_lots(all_lots_list)

        if not target_lot.is_viable:
            target_lot.dynamic_ceiling_eur = 0
            return 0

        purchasing_power = ledger.max_purchasing_power_for_lot(target_lot.lot_id)
        reserve_eur = self.compute_portfolio_reserve(
            target_lot,
            all_lots_list,
            purchasing_power,
        )

        ceiling = min(target_lot.max_bid_eur, max(0, purchasing_power - reserve_eur))
        target_lot.dynamic_ceiling_eur = ceiling
        return ceiling

    def refresh_all_ceilings(
        self,
        all_lots: Iterable[WatchLot],
        ledger: BudgetLedger,
    ) -> dict[str, int]:
        """Recalculates pruning and dynamic ceilings for all lots in the portfolio."""
        lots_list = list(all_lots)
        self.prune_lots(lots_list)
        result: dict[str, int] = {}
        for lot in lots_list:
            result[lot.lot_id] = self.compute_dynamic_ceiling(lot, lots_list, ledger)
        return result

