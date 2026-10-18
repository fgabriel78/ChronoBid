# Design

## Context

En `src/chronobid/orchestrator.py`, la vigilancia en Modo Patrulla está centralizada en `run_patrol_step()` y en el bucle principal `run_until_complete()`. Actualmente, el bucle duerme un intervalo fijo (`patrol_interval_seconds = 15.0`) y en cada ciclo consulta secuencialmente todos los lotes activos que no estén en Modo Asalto.

Véase `proposal.md` para la motivación de sustituir este intervalo fijo por una cadencia escalonada adaptativa.

## Goals / Non-Goals

**Goals:**
- Implementar una política de cadencia escalonada por tramos de tiempo restante:
  - $> 3.600\text{ s}$ ($> 1\text{ hora}$): cada $300\text{ s}$ ($5\text{ min}$).
  - $900\text{ s} < t \le 3.600\text{ s}$ ($15\text{ a }60\text{ min}$): cada $120\text{ s}$ ($2\text{ min}$).
  - $300\text{ s} < t \le 900\text{ s}$ ($5\text{ a }15\text{ min}$): cada $60\text{ s}$ ($1\text{ min}$).
- Mantener un registro en memoria (`_last_patrolled_at: dict[str, float]`) de la última consulta de cada lote mediante `time.monotonic()`.
- Priorizar la consulta inmediata de lotes nuevos o en estado `PENDING` sin snapshot previo.
- Preservar la anticipación adaptativa exacta para despertar en el segundo $300$ cuando un lote viable cruce el umbral de Modo Asalto.
- Ajustar el tiempo de espera del bucle principal al tiempo mínimo que reste para la siguiente consulta requerida, acotado por un tope de seguridad.

**Non-Goals:**
- No modificar el comportamiento de Modo Asalto ($\le 300\text{ s}$), que continuará manteniendo pestañas dedicadas a $0.25\text{ s}$ de refresco continuo y ventana de snipe $\le 25\text{ s}$.
- No modificar la lógica de contabilidad atómica de `BudgetLedger` ni la optimización de carteras de `PortfolioOptimizer`.
- No alterar la persistencia write-behind en Excel de `ExcelSyncWorker`.

## Decisions

### Decisión 1: Rastreo temporal en `AuctionOrchestrator._last_patrolled_at`
- Se mantendrá un diccionario `self._last_patrolled_at: dict[str, float] = {}` en el orquestador que asocia `lot_id` con el timestamp monotónico (`time.monotonic()`) de su última consulta finalizada con éxito o fallo transitorio.
- **Razón**: Desacopla la lógica de planificación de red del modelo de dominio `WatchLot`, evitando añadir campos transitorios de transporte al modelo de subasta. Además, al eliminar lotes en caliente durante la reconciliación con Excel, las entradas se limpian de inmediato.
- **Alternativas consideradas**:
  - *Campo `last_patrolled_at` en `WatchLot`*: Descartado para mantener `WatchLot` enfocado en datos del lote y de mercado, sin acoplarlo al reloj monotónico de ejecución del proceso.

### Decisión 2: Función de resolución de cadencia escalonada
Se añade el método auxiliar:
```python
def get_patrol_interval_for_lot(self, lot: WatchLot) -> float:
    if lot.seconds_remaining is None:
        return 0.0
    if lot.seconds_remaining > 3600.0:
        return 300.0
    if lot.seconds_remaining > 900.0:
        return 120.0
    if lot.seconds_remaining > self.assault_threshold_seconds:
        return 60.0
    return 0.0
```
- **Razón**: Es una función determinista y testeable de forma aislada.

### Decisión 3: Selección de lotes a consultar en `run_patrol_step()`
En cada invocación de `run_patrol_step()`:
- Un lote es candidato si está activo, no cerrado y no tiene tarea de asalto en curso.
- Se consulta si cumple alguna de las siguientes condiciones:
  1. No tiene registro previo en `self._last_patrolled_at` (lote recién iniciado o PENDING).
  2. Su `seconds_remaining` es desconocido (`None`).
  3. `now - self._last_patrolled_at[lot.lot_id] >= self.get_patrol_interval_for_lot(lot)`.
- Si ningún lote cumple la condición de consulta, el paso concluye de inmediato sin abrir pestañas innecesarias.

### Decisión 4: Cálculo del tiempo de espera del bucle principal
En `_calculate_patrol_wait_seconds()`:
- Para cada lote activo no en asalto, se calcula el tiempo restante hasta su próxima consulta:
  $$\text{time\_to\_poll} = \max(0.5,\ \text{intervalo} - (\text{now} - \text{last\_polled}))$$
- Si el lote es viable y está en tramo próximo ($300 < t < 300 + \text{intervalo}$), se evalúa también:
  $$\text{time\_to\_assault} = \max(0.5,\ t - \text{assault\_threshold\_seconds})$$
- El tiempo de espera retornado será:
  $$\text{wait} = \min(\text{todos los } \text{time\_to\_poll} \text{ y } \text{time\_to\_assault})$$
  acotado por un máximo configurable de seguridad (ej. $60.0\text{ s}$) y un mínimo de $0.5\text{ s}$.
- **Razón**: Asegura que el bucle solo duerme lo estrictamente necesario hasta el próximo evento relevante, manteniendo la comprobación reactiva de Excel cada 2 segundos vía `_wait_interval_with_reload_check`.

## Risks / Trade-offs

- **Riesgo**: Desfase en el reloj de Catawiki durante esperas largas de 5 minutos en el tramo lejano.
  - *Mitigación*: Al entrar en la última hora, las revisiones pasan a ser cada 2 minutos, y en los últimos 15 minutos pasan a ser cada 60 segundos con sincronización sub-segundo, garantizando que el reloj esté perfectamente alineado antes de los 5 minutos finales.
- **Riesgo**: Errores transitorios de red que retrasen la consulta.
  - *Mitigación*: Si la consulta falla con `CatawikiPageLoadError`, se actualiza igualmente `_last_patrolled_at` con un retroceso temporal o se reintenta tras un intervalo breve (ej. $15\text{ s}$ o en la siguiente vuelta del bucle) para garantizar que un fallo no deje el lote sin monitorizar durante 5 minutos.
