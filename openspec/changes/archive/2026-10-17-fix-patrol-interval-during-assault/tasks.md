# Tasks

## 1. Implementación del Desacoplo de Intervalos en el Orquestador

- [x] 1.1 Implementar los helpers `_get_pending_patrol_lots()` y `_calculate_patrol_wait_seconds()` en `src/chronobid/orchestrator.py` para identificar lotes que requieren patrulla y calcular el tiempo de espera con anticipación adaptativa al umbral de 300 segundos.
- [x] 1.2 Refactorizar el bucle `run_until_complete()` en `src/chronobid/orchestrator.py` desacoplando el intervalo de patrulla respecto a las tareas de asalto concurrentes, empleando espera pasiva sin busy-wait cuando todos los lotes estén en asalto y cancelación reactiva sobre `_stop_event`.

## 2. Pruebas y Verificación

- [x] 2.1 Añadir tests en `tests/test_orchestrator_e2e.py` con escenarios mixtos concurrentes (un reloj en Modo Asalto a alta frecuencia y otro en Modo Patrulla), verificando que las consultas al lote de patrulla respetan rigurosamente el intervalo espaciado y no se aceleran.
- [x] 2.2 Ejecutar la suite completa de pruebas con `pytest` y verificar que todos los tests pasan correctamente.
