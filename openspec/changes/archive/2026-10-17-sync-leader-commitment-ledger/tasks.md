# Tasks

## 1. Sincronización de compromisos de líder en BudgetLedger

- [x] 1.1 Añadir el método asíncrono `sync_leader_commitment(lot_id: str, committed_amount_eur: int) -> int` en `src/chronobid/strategy/ledger.py` protegido por `self._lock` para registrar o actualizar el saldo comprometido de un lote activo donde el usuario ya es líder (acotando al disponible máximo si excediera el presupuesto global y liberando si `<= 0`), y verificar su funcionamiento con `pytest tests/test_ledger.py`.
- [x] 1.2 Añadir tests unitarios en `tests/test_ledger.py` que cubran la sincronización de pujas manuales/previas como líder, actualizaciones de importe comprometido y liberación posterior, verificando que todos los tests pasan con `pytest tests/test_ledger.py -v`.

## 2. Reconciliación contable en Motor Táctico y Modo Patrulla

- [x] 2.1 Actualizar `TacticalBiddingEngine.process_tick_and_decide` en `src/chronobid/strategy/tactics.py` para sincronizar `ledger.sync_leader_commitment(lot.lot_id, lot.current_bid_eur)` cuando `lot.is_leader` es `True` en un lote abierto (además de liberar cuando `not lot.is_leader`), y verificar con `pytest tests/test_tactics.py -v`.
- [x] 2.2 Actualizar `AuctionOrchestrator.run_patrol_step` en `src/chronobid/orchestrator.py` para reconciliar el estado de cada lote con `BudgetLedger` tras `lot.apply_market_snapshot(...)` también cuando el lote permanece abierto en Modo Patrulla (comprometiendo el saldo si `is_leader=True` o liberándolo si perdió el liderazgo), antes de invocar `self._emit_and_enqueue()`, y verificar con `pytest tests/test_orchestrator_e2e.py -v`.
- [x] 2.3 Añadir tests en `tests/test_tactics.py` y `tests/test_orchestrator_e2e.py` que simulen un lote en Modo Patrulla con liderazgo previo (`is_leader=True, current_bid_eur=1`) y su posterior superación por otro postor en patrulla, verificando que `ledger.committed_leader_eur`, `ledger.available_eur` y las celdas `B3`/`B4` del Excel se actualizan correctamente al ejecutar `pytest -v`.
