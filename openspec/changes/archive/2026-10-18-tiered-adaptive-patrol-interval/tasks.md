# Tasks

## 1. Modelo de Cadencia y Filtrado de Lotes en Patrulla

- [x] 1.1 Implementar `get_patrol_interval_for_lot(lot: WatchLot)` y el diccionario `_last_patrolled_at: dict[str, float]` en `AuctionOrchestrator` (`src/chronobid/orchestrator.py`), verificando que retorne 300s para >3600s, 120s para 900s-3600s, 60s para 300s-900s y 0s para lotes nuevos o sin tiempo restante.
- [x] 1.2 Actualizar `run_patrol_step()` en `src/chronobid/orchestrator.py` para filtrar y consultar únicamente aquellos lotes cuyo intervalo asignado haya vencido o que no tengan consulta previa registrada, actualizando `_last_patrolled_at[lot.lot_id]` tras cada intento.

## 2. Planificación Dinámica del Tiempo de Espera

- [x] 2.1 Actualizar `_calculate_patrol_wait_seconds()` en `src/chronobid/orchestrator.py` para calcular el tiempo mínimo restante hasta la próxima consulta programada entre todos los lotes activos en patrulla, preservando la anticipación adaptativa exacta al umbral de 300 segundos para lotes próximos a Modo Asalto.
- [x] 2.2 Sincronizar la limpieza de `_last_patrolled_at` en `reconcile_with_excel()` cuando se den de baja lotes o cuando se reactiven tras incremento de tope de puja máxima.

## 3. Verificación y Suite de Tests E2E

- [x] 3.1 Añadir y actualizar tests en `tests/test_orchestrator_e2e.py` para verificar que la cadencia por tramos se cumple rigurosamente, que los lotes lejanos no se consultan en pasadas intermedias y que la anticipación a los 300s despierta con exactitud.
- [x] 3.2 Ejecutar la suite completa de pruebas con `pytest tests/ -v` y validar que todos los tests pasan sin regresiones.
