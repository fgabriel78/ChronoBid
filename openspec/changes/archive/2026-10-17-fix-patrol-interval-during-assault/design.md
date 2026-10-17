# Design

## Context

Véase `proposal.md` para la motivación. Actualmente, `AuctionOrchestrator.run_until_complete()` en `src/chronobid/orchestrator.py` ejecuta `run_patrol_step()` y随后 evalúa:
```python
if any(not t.done() for t in self._assault_tasks.values()):
    await asyncio.sleep(min(self.patrol_interval_seconds, 0.05))
elif self.has_active_lots():
    await asyncio.sleep(self.patrol_interval_seconds)
```
Dado que `any(...)` es verdadero tan pronto como un reloj entra en Modo Asalto, la espera de patrulla colapsa de 15 segundos a 50 ms para el resto de la ejecución, bombardeando a Catawiki con consultas a los relojes en patrulla.

## Goals / Non-Goals

**Goals:**
- Desacoplar completamente el intervalo del bucle de patrulla respecto al estado de las tareas de asalto.
- Garantizar que los lotes en Modo Patrulla solo se consulten cada `patrol_interval_seconds` (15 s por defecto) o según anticipación adaptativa cuando se aproximen a los 5 minutos finales.
- Eliminar el *busy-wait* (ciclos vacíos cada 50 ms) cuando todos los lotes viables están en Modo Asalto.
- Responder de forma inmediata a la señal de parada (`_stop_event`) sin quedar bloqueado en esperas largas de 15 s.

**Non-Goals:**
- No alterar la lógica interna táctica ni de pujas de `_run_assault_for_lot`.
- No modificar los selectores DOM ni la extracción de snapshots en `CatawikiLotPage`.
- No alterar los contratos ni el formato de sincronización con Excel.

## Decisions

### Decisión 1: Filtrado explícito de lotes pendientes de patrulla y desacoplo de espera
- **Enfoque**: Definir un helper `_has_pending_patrol_lots()` que devuelva `True` si y solo si existe algún lote activo (`lot.status.is_active and not lot.is_closed`) cuyo `lot_id` no esté ya ejecutándose en una tarea activa de `_assault_tasks`.
- **Comportamiento**:
  - Si hay lotes pendientes de patrulla: ejecutar `run_patrol_step()` y esperar el intervalo calculado de patrulla.
  - Si todos los lotes viables ya están en asalto: omitir la llamada a `run_patrol_step()` y esperar pasivamente mediante `asyncio.wait(active_assault_tasks, return_when=asyncio.FIRST_COMPLETED, timeout=0.5)`.
- **Alternativas descartadas**:
  - *Mantener un solo `asyncio.sleep(0.05)`*: Provoca sobrecarga masiva de CPU y peticiones a Catawiki en Chrome.
  - *Crear un worker o hilo separado*: Innecesario y añade complejidad de sincronización sobre `self.lots` y `self.ledger`. La coordinación dentro de `run_until_complete` con `asyncio` es suficiente y limpia.

### Decisión 2: Anticipación adaptativa al umbral de asalto
- **Enfoque**: Calcular el tiempo de espera como:
  ```python
  wait_time = self.patrol_interval_seconds
  for lot in pending_patrol_lots:
      if lot.seconds_remaining and lot.seconds_remaining > self.assault_threshold_seconds:
          time_to_assault = lot.seconds_remaining - self.assault_threshold_seconds
          if 0 < time_to_assault < wait_time:
              wait_time = max(0.5, time_to_assault)
  ```
- **Racional**: Si un reloj tiene 303 s restantes (a 3 s de conmutar a asalto a los 300 s), el orquestador no esperará 15 s (lo que retrasaría la entrada a asalto hasta los 288 s), sino que esperará 3 s y abrirá su pestaña dedicada en el segundo exacto.

### Decisión 3: Espera reactiva vinculada a `_stop_event`
- **Enfoque**: En lugar de `asyncio.sleep(wait_time)`, utilizar:
  ```python
  try:
      await asyncio.wait_for(self._stop_event.wait(), timeout=wait_time)
  except asyncio.TimeoutError:
      pass
  ```
- **Racional**: Permite que `orchestrator.request_stop()` (ej. al pulsar Ctrl+C en la CLI) interrumpa la espera de 15 segundos instantáneamente y cierre la aplicación de forma limpia y fluida.

## Risks / Trade-offs

- **[Riesgo] Relojes con tiempo restante desincronizado o estimado**: Si un reloj reporta `seconds_remaining` impreciso en la lectura inicial, el cálculo adaptativo podría dispararse antes o después.
  - *Mitigación*: Se establece una cota mínima de 0.5 s para el cálculo adaptativo y, una vez en asalto, la pestaña dedicada asume el control en tiempo real con lectura continua de sub-segundo.
