"""Gestor atómico de presupuesto en tiempo real (BudgetLedger) con asyncio.Lock."""

from __future__ import annotations

import asyncio

from chronobid.models.auction import GlobalBudgetState


class BudgetLedger:
    """
    Contabilidad atómica del presupuesto global en euros:
        Disponible = Global - Gastado en firme (Ganados) - Comprometido (Líder provisional)

    Todas las operaciones de reserva, liberación y adjudicación están protegidas
    por un único asyncio.Lock para impedir condiciones de carrera entre pestañas
    concurrentes en Modo Asalto.
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
        """Suma total de relojes ya ganados en firme."""
        return sum(self._won_lots.values())

    @property
    def committed_leader_eur(self) -> int:
        """Suma total de pujas activas en las que el usuario es líder provisional."""
        return sum(self._committed_lots.values())

    @property
    def available_eur(self) -> int:
        """Saldo disponible actual para nuevas pujas o incrementos diferenciales."""
        return max(0, self._global_budget_eur - self.spent_eur - self.committed_leader_eur)

    def get_committed(self, lot_id: str) -> int:
        """Devuelve el importe actualmente comprometido como líder en `lot_id`."""
        return self._committed_lots.get(lot_id, 0)

    def get_won(self, lot_id: str) -> int:
        """Devuelve el importe adjudicado en firme para `lot_id` (0 si no ganado)."""
        return self._won_lots.get(lot_id, 0)

    def max_purchasing_power_for_lot(self, lot_id: str) -> int:
        """
        Devuelve el importe total máximo que podría pujar `lot_id` con el saldo actual,
        sumando el saldo libre más lo que `lot_id` ya tiene comprometido como líder.
        """
        return self.available_eur + self.get_committed(lot_id)

    def snapshot(self) -> GlobalBudgetState:
        """Genera una instantánea consistente del estado del presupuesto."""
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
        Intenta comprometer de forma atómica el importe `new_bid_eur` para `lot_id`.

        - Si `max_lot_bid_eur` se especifica y `new_bid_eur > max_lot_bid_eur`, rechaza.
        - Si el lote ya tenía un compromiso previo menor (p. ej. escalera de reserva),
          descuenta únicamente el incremento diferencial (`new_bid_eur - prev_committed`).
        - Devuelve True si se reservó el saldo, False si excede el techo o el presupuesto disponible.
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
                # Ya tenía reservado al menos ese importe
                self._committed_lots[lot_id] = new_bid_eur
                return True

            if delta > self.available_eur:
                return False

            self._committed_lots[lot_id] = new_bid_eur
            return True

    async def release(self, lot_id: str) -> int:
        """
        Libera al instante el importe comprometido en `lot_id` (cuando otro postor
        supera la puja o falla el envío en UI o termina sin adjudicarse por reserva).
        Devuelve el importe liberado en euros.
        """
        async with self._lock:
            return self._committed_lots.pop(lot_id, 0)

    async def confirm_won(self, lot_id: str, final_amount_eur: int | None = None) -> int:
        """
        Convierte el compromiso provisional de `lot_id` en gasto en firme tras ganar la subasta.
        Devuelve el importe registrado como ganado.
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
        """Actualiza el presupuesto global en caliente si no es inferior a lo ya comprometido+gastado."""
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
