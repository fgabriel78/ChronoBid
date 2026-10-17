# Design

## Context

Actualmente (véase `proposal.md` - Why), el modelo `WatchLot` y el libro contable `BudgetLedger` siguen caminos separados durante las lecturas de mercado:
1. `WatchLot.apply_market_snapshot()` actualiza `lot.is_leader = True`, `lot.status = AuctionStatus.LEADER` y deduce `lot.my_last_bid_eur = lot.current_bid_eur` en cuanto Catawiki reporta que el usuario va en cabeza.
2. Sin embargo, `BudgetLedger._committed_lots` solo se modifica cuando `TacticalBiddingEngine.process_tick_and_decide()` invoca `ledger.try_reserve()` justo antes de enviar una autopuja en la ventana final (`< 25s`), o cuando libera/confirma un lote en Modo Asalto o al cierre.
3. Además, `AuctionOrchestrator.run_patrol_step()` solo invoca `tactics.process_tick_and_decide()` si el lote ha cerrado (`lot.is_closed or lot.status.is_terminal`), omitiendo cualquier reconciliación contable en lotes abiertos durante el Modo Patrulla (`> 300s`).

## Goals / Non-Goals

**Goals:**
- Garantizar que `BudgetLedger` refleje en todo momento el compromiso real (`lot.current_bid_eur`) de cualquier lote abierto en el que `lot.is_leader` sea `True`, tanto en Modo Patrulla como en Modo Asalto.
- Liberar inmediatamente en `BudgetLedger` el compromiso de un lote si pasa a `lot.is_leader == False` durante una ronda de Modo Patrulla o un tick de Modo Asalto.
- Mantener la atomicidad protegida por `asyncio.Lock` y el invariante `GlobalBudgetState` sin romper el flujo de reserva previa (`try_reserve`) en autopujas concurrentes.

**Non-Goals:**
- No se modifica la regla táctica de invisibilidad previa (`< 25s`) ni la fórmula de estimación de costes de `PortfolioOptimizer`.
- No se altera el formato de columnas del fichero Excel (`.xlsx`).

## Decisions

1. **Método de sincronización atómica `BudgetLedger.sync_leader_commitment(lot_id, committed_amount_eur)`**:
   - **Decisión**: Añadir un método asíncrono en `BudgetLedger` protegido por `self._lock` que sincronice el importe comprometido real de un lote activo cuando Catawiki reporta que el usuario es líder (`is_leader == True`), o lo libere si `committed_amount_eur <= 0`.
   - **Racional**: A diferencia de `try_reserve()` (que es una puerta de autorización previa antes de enviar una nueva autopuja y debe rechazar si supera `max_lot_bid_eur`), la sincronización de una puja donde el usuario *ya es líder* en Catawiki representa un hecho consumado en la plataforma (p. ej. una puja manual de 1 € realizada por el usuario o una sesión reanudada). Si el importe cabe en el presupuesto global disponible, registra `self._committed_lots[lot_id] = committed_amount_eur`; si por intervención manual externa excediera el disponible restante, acota el registro al máximo disponible (`prev_committed + self.available_eur`) para preservar el invariante `spent_eur + committed_leader_eur <= global_budget_eur` en `GlobalBudgetState`.
   - **Alternativa descartada**: Reutilizar `try_reserve(..., max_lot_bid_eur=ceiling)` para sincronizar lecturas pasivas. Se descarta porque si el techo dinámico $E_i$ baja temporalmente por reserva de cartera o si el usuario pujó manualmente por encima de $E_i$, `try_reserve` devolvería `False` y dejaría `committed_leader_eur` en `0 €` pese a estar el dinero realmente comprometido en Catawiki.

2. **Reconciliación unificada en `TacticalBiddingEngine` y `AuctionOrchestrator.run_patrol_step`**:
   - **Decisión**:
     - En `TacticalBiddingEngine.process_tick_and_decide()`, antes de calcular el techo dinámico con `optimizer.compute_dynamic_ceiling()`:
       - Si `lot.is_leader` es `True` y `lot.current_bid_eur > 0`, invocar `await ledger.sync_leader_commitment(lot.lot_id, lot.current_bid_eur)`.
       - Si `not lot.is_leader` y `ledger.get_committed(lot.lot_id) > 0`, invocar `await ledger.release(lot.lot_id)`.
     - En `AuctionOrchestrator.run_patrol_step()`, tras `lot.apply_market_snapshot(...)`, reconciliar siempre el lote con `BudgetLedger` (o invocar un método de reconciliación en `tactics` / `ledger`) tanto si el lote sigue abierto como si ha cerrado, antes de llamar a `self._emit_and_enqueue()`.
   - **Racional**: Asegura que `PortfolioOptimizer.refresh_all_ceilings()`, el panel en vivo de Rich (`cli.py`) y el `ExcelSyncWorker` reciban una instantánea `ledger.snapshot()` ya actualizada en el mismo ciclo de patrulla o asalto.

## Risks / Trade-offs

- **[Risk] Descuento doble al calcular el techo dinámico del propio lote líder** → **Mitigation**: `BudgetLedger.max_purchasing_power_for_lot(lot_id)` ya suma `self.available_eur + self.get_committed(lot_id)`, y `PortfolioOptimizer.compute_portfolio_reserve()` ya ignora lotes con `lot.is_leader == True`. Por tanto, registrar el compromiso en `_committed_lots` descuenta el saldo para los *demás* relojes sin reducir el poder de compra disponible para el propio reloj líder.
- **[Risk] Puja manual externa que supere el presupuesto global configurado en Excel** → **Mitigation**: `sync_leader_commitment` acota el compromiso registrado a `prev_committed + self.available_eur` para no violar nunca la validación de `GlobalBudgetState` (`spent + committed <= global`).
