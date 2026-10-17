# Proposal

## Why

Cuando el usuario ya es el máximo pujador provisional de un lote en Catawiki (por ejemplo, tras realizar una puja manual previa o al iniciar/reiniciar ChronoBid durante el Modo Patrulla), la fila del lote se marca como `LIDER` con su importe en `Mi Puja`, pero `BudgetLedger` mantiene `Comprometido (Líder): 0 €` porque solo registraba reservas al ejecutar autopujas en los últimos 25 segundos. Esto desincroniza la contabilidad atómica en pantalla y en el Excel, sobrestimando el saldo disponible real y omitiendo también la liberación de saldo si el usuario es superado durante la patrulla.

## What Changes

- Reconciliar de forma atómica el estado de cada lote activo con `BudgetLedger` tras cada lectura válida de mercado, tanto en **Modo Patrulla** como en **Modo Asalto**.
- Añadir en `BudgetLedger` un mecanismo de sincronización de compromiso de líder (`sync_leader_commitment` / reconciliación de lote) que refleje el importe actual comprometido cuando `lot.is_leader` es `True` y `lot.current_bid_eur > 0` (incluso si procede de una puja manual previa o lectura en patrulla), y libere el compromiso inmediatamente cuando `lot.is_leader` pase a `False`.
- Actualizar `AuctionOrchestrator.run_patrol_step` y `TacticalBiddingEngine.process_tick_and_decide` para asegurar que la contabilidad atómica (`committed_leader_eur`, `spent_eur` y `available_eur`) esté siempre sincronizada antes de recalcular techos dinámicos (`PortfolioOptimizer`), renderizar el panel Rich y encolar la escritura en Excel.

## Capabilities

### New Capabilities
<!-- Ninguna capacidad nueva; se amplía la capacidad existente de contabilidad y estrategia de cartera. -->

### Modified Capabilities
- `bidding-strategy-portfolio`: Ampliar el requisito de contabilidad atómica de presupuesto en vuelo (`Ledger`) para exigir la sincronización automática de compromisos de líder detectados en lecturas de mercado (Modo Patrulla y Modo Asalto, incluidas pujas previas o manuales) y su liberación al perder el liderazgo en cualquier modo.

## Impact

- `src/chronobid/strategy/ledger.py`: Método atómico para sincronizar el compromiso real de un lote abierto cuando el usuario figura como líder provisional.
- `src/chronobid/strategy/tactics.py`: Sincronización del compromiso en `BudgetLedger` durante `process_tick_and_decide` cuando el usuario ya es líder.
- `src/chronobid/orchestrator.py`: Reconciliación del `BudgetLedger` tras aplicar cada `LotPageSnapshot` en `run_patrol_step` (no solo cuando el lote está cerrado).
- `tests/test_ledger.py`, `tests/test_tactics.py`, `tests/test_orchestrator_e2e.py`: Nuevos casos de prueba para liderazgo detectado en Modo Patrulla, pujas manuales previas y pérdida de liderazgo durante patrulla.
