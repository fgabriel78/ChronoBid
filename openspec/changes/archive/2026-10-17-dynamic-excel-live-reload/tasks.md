# Tasks

## 1. Actualización Atómica de Presupuesto y Firma de Entradas

- [x] 1.1 Añadir el método `update_global_budget(new_budget_eur: int)` a `BudgetLedger` en `src/chronobid/strategy/ledger.py` y verificar su comportamiento mediante tests unitarios en `tests/test_ledger.py`.
- [x] 1.2 Implementar `extract_input_signature()` y los comparadores de entradas en `src/chronobid/excel/workbook_io.py`, con captura tolerante de `PermissionError`/`OSError`, verificándolo mediante tests en `tests/test_workbook_io.py`.

## 2. Reconciliación en Caliente en el Orquestador

- [x] 2.1 Implementar `reconcile_with_excel()` y `_check_and_reload_excel()` en `AuctionOrchestrator` (`src/chronobid/orchestrator.py`) para sincronizar el presupuesto en el ledger, añadir nuevos lotes, actualizar topes, reactivar lotes viables y remover lotes eliminados.
- [x] 2.2 Integrar el ciclo de sondeo periódico de recarga y el evento de despertar reactivo de patrulla en `run_until_complete()` dentro de `src/chronobid/orchestrator.py`.

## 3. Pruebas Automatizadas y Verificación End-to-End

- [x] 3.1 Añadir tests en `tests/test_orchestrator_e2e.py` simulando la edición dinámica del archivo Excel en disco durante la ejecución (alta de reloj, cambio de presupuesto B1, reactivación de lote por aumento de tope y borrado de fila).
- [x] 3.2 Ejecutar la suite completa de pruebas con `pytest` y verificar que todos los tests pasan correctamente.
