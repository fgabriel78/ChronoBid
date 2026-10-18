"""Real-time atomic budget manager (BudgetLedger) using asyncio.Lock."""

from __future__ import annotations

import asyncio

from chronobid.models.auction import GlobalBudgetState


class BudgetLedger:
    """
    Atomic accounting of the global budget in euros:
        Available = Global - Firmly spent (Won) - Committed (Provisional leader)

    All reservation, release, and award operations are protected by a single
    asyncio.Lock to prevent race conditions across concurrent Assault Mode tabs.
    """

    def __init__(self, global_budget_eur: int) -> None:
        if global_budget_eur <= 0:
            raise ValueError("El presupuesto global debe ser mayor que 0 EUR.")
        self._global_budget_eur: int = global_budget_eur
        self._won_lots: dict[str, int] = {}
        self._committed_lots: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def global_budget_eur(self) -> int:
        return self._global_budget_eur

    @property
    def spent_eur(self) -> int:
        """Total sum of watches firmly won."""
        return sum(self._won_lots.values())

    @property
    def committed_leader_eur(self) -> int:
        """Total sum of active bids where the user is provisional leader."""
        return sum(self._committed_lots.values())

    @property
    def available_eur(self) -> int:
        """Current available balance for new bids or differential increments."""
        return max(0, self._global_budget_eur - self.spent_eur - self.committed_leader_eur)

    def get_committed(self, lot_id: str) -> int:
        """Returns the amount currently committed as leader on `lot_id`."""
        return self._committed_lots.get(lot_id, 0)

    def get_won(self, lot_id: str) -> int:
        """Returns the firmly awarded amount for `lot_id` (0 if not won)."""
        return self._won_lots.get(lot_id, 0)

    def max_purchasing_power_for_lot(self, lot_id: str) -> int:
        """
        Returns the maximum total amount `lot_id` could bid with current balance,
        adding free balance plus what `lot_id` already has committed as leader.
        """
        return self.available_eur + self.get_committed(lot_id)

    def snapshot(self) -> GlobalBudgetState:
        """Generates a consistent snapshot of the budget state."""
        return GlobalBudgetState(
            global_budget_eur=self._global_budget_eur,
            spent_eur=self.spent_eur,
            committed_leader_eur=self.committed_leader_eur,
        )

    async def try_reserve(
        self,
        lot_id: str,
        new_bid_eur: int,
        *,
        max_lot_bid_eur: int | None = None,
    ) -> bool:
        """
        Atomically attempts to commit `new_bid_eur` for `lot_id`.

        - If `max_lot_bid_eur` is specified and `new_bid_eur > max_lot_bid_eur`, rejects.
        - If the lot already had a smaller prior commitment (e.g., reserve ladder),
          deducts only the differential increment (`new_bid_eur - prev_committed`).
        - Returns True if balance was reserved, False if exceeding ceiling or available budget.
        """
        if new_bid_eur <= 0:
            return False
        if max_lot_bid_eur is not None and new_bid_eur > max_lot_bid_eur:
            return False

        async with self._lock:
            if lot_id in self._won_lots:
                return False

            prev_committed = self._committed_lots.get(lot_id, 0)
            delta = new_bid_eur - prev_committed
            if delta <= 0:
                # Already had at least this amount reserved
                self._committed_lots[lot_id] = new_bid_eur
                return True

            if delta > self.available_eur:
                return False

            self._committed_lots[lot_id] = new_bid_eur
            return True

    async def release(self, lot_id: str) -> int:
        """
        Instantly releases the amount committed on `lot_id` (when another bidder
        outbids, UI submission fails, or ends unawarded due to reserve).
        Returns the released amount in euros.
        """
        async with self._lock:
            return self._committed_lots.pop(lot_id, 0)

    async def sync_leader_commitment(self, lot_id: str, committed_amount_eur: int) -> int:
        """
        Atomically synchronizes the actual amount committed on `lot_id` when
        a market reading confirms that the user is currently the provisional leader.

        - If `committed_amount_eur <= 0`, releases any prior lot commitment.
        - If the lot is already firmly won, leaves `_committed_lots` untouched.
        - Bounds commitment to maximum available balance to always preserve
          the `spent_eur + committed_leader_eur <= global_budget_eur` invariant.
        - Returns the amount effectively registered as committed.
        """
        async with self._lock:
            if lot_id in self._won_lots:
                return 0
            if committed_amount_eur <= 0:
                self._committed_lots.pop(lot_id, None)
                return 0

            prev_committed = self._committed_lots.get(lot_id, 0)
            max_allowable = prev_committed + self.available_eur
            effective_committed = min(committed_amount_eur, max_allowable)
            if effective_committed <= 0:
                self._committed_lots.pop(lot_id, None)
                return 0

            self._committed_lots[lot_id] = effective_committed
            return effective_committed

    async def confirm_won(self, lot_id: str, final_amount_eur: int | None = None) -> int:
        """
        Converts the provisional commitment of `lot_id` into firm expenditure after winning the auction.
        Returns the amount recorded as won.
        """
        async with self._lock:
            prev_committed = self._committed_lots.pop(lot_id, 0)
            amount = final_amount_eur if final_amount_eur is not None else prev_committed
            if amount <= 0:
                return 0
            if amount > self.available_eur:
                raise ValueError(
                    f"El importe ganado ({amount} €) para {lot_id} excede el presupuesto global."
                )
            self._won_lots[lot_id] = amount
            return amount

    async def update_global_budget(self, new_budget_eur: int) -> GlobalBudgetState:
        """Updates the global budget live if not lower than already committed+spent."""
        if new_budget_eur <= 0:
            raise ValueError("El presupuesto global debe ser mayor que 0 EUR.")
        async with self._lock:
            locked_total = self.spent_eur + self.committed_leader_eur
            if new_budget_eur < locked_total:
                raise ValueError(
                    f"El nuevo presupuesto ({new_budget_eur} €) es menor que la suma ya "
                    f"gastada y comprometida ({locked_total} €)."
                )
            self._global_budget_eur = new_budget_eur
            return self.snapshot()
