# Design

## Context

Véase `proposal.md` para la motivación. Actualmente, `read_workbook_config()` se ejecuta únicamente en el constructor de `AuctionOrchestrator`, mientras que `ExcelSyncWorker` realiza escrituras asíncronas periódicas de salida (columnas C:J y B2:B4) mediante `write_workbook_state()`. Cuando el usuario edita `relojes.xlsx` (cambia el presupuesto en B1, añade o borra filas, o modifica las pujas máximas en la columna B), la aplicación ignora los cambios porque nunca vuelve a leer del disco.

## Goals / Non-Goals

**Goals:**
- Detectar en 1-2 segundos cualquier modificación guardada por el usuario en el fichero Excel.
- Distinguir de forma infalible entre las escrituras de salida del propio ChronoBid y las ediciones de entrada realizadas por el usuario.
- Reconciliar atómicamente en memoria el presupuesto global (`BudgetLedger`), los techos dinámicos (`PortfolioOptimizer`), altas, bajas y reactivaciones de relojes.
- Tolerar bloqueos de archivo transitorios en Windows (`PermissionError` o códigos de compartición `winerror 32/33`) sin interrumpir las pujas activas.
- Despertar inmediatamente la ronda de patrulla ante la adición de nuevos lotes.

**Non-Goals:**
- No reemplazar el formato estándar del archivo Excel ni modificar la plantilla existente.
- No sobreescribir las celdas de entrada del usuario (B1 y columnas A:B) durante la persistencia de salida.
- No interferir con las tareas de asalto en curso salvo para cancelar lotes que el usuario haya eliminado explícitamente.

## Decisions

### Decisión 1: Firma de Entradas (`InputSignature`) para discriminar escrituras internas vs externas
- **Problema**: Cada vez que `ExcelSyncWorker` guarda el estado en disco, la fecha de modificación (`st_mtime`) del archivo cambia. Si el orquestador reaccionara ciegamente a cada cambio de `mtime`, entraría en un ciclo continuo de recarga redundante.
- **Solución**: Definir una firma inmutable de las celdas de entrada:
  ```python
  InputSignature = tuple[int, tuple[tuple[str, int, int], ...]]
  # (global_budget_eur, ((url, max_bid_eur, row_idx), ...))
  ```
  Al detectar un cambio en `st_mtime`, se leen exclusivamente las celdas de entrada (B1 y A:B). Si la firma resultante es idéntica a la firma de las entradas activas en memoria, se ignora (fue una escritura de salida de ChronoBid). Si difiere, se dispara la reconciliación.

### Decisión 2: Método de reconciliación atómica en `AuctionOrchestrator`
- **Reconciliación de Presupuesto**:
  - Incorporar en `BudgetLedger` el método `update_global_budget(new_budget_eur: int)`.
  - Si B1 cambia, se actualiza el presupuesto en el ledger. El saldo disponible se reajusta automáticamente al instante.
- **Emparejamiento de Lotes por URL**:
  - Se mapean los lotes existentes por su `url` normalizada.
  - **Lotes existentes**: Se actualiza `row_index` y `max_bid_eur`. Si el lote estaba en `DISCARDED_MAX_EXCEEDED` pero el nuevo tope es $\ge$ a su `next_min_bid_eur` y la subasta no ha concluido, se **reactiva** automáticamente a `PATROL` (o `ASSAULT` si $\le 300$ s). Si el tope baja por debajo de `next_min_bid_eur` y no somos líderes, se descarta.
  - **Nuevos lotes**: Se instancian en estado `PENDING` y se agregan a `self.lots`. Se activa el evento de despertar para consultar sus datos en Catawiki inmediatamente.
  - **Lotes eliminados**: Si una URL desaparece del Excel, se retira de `self.lots`. Si tenía una tarea de asalto dedicada (`_assault_tasks`), se cancela y se cierra su pestaña de navegación. Si tenía presupuesto comprometido como líder provisional, se libera en el ledger.
- **Sincronización de Salida y UI**:
  - Al finalizar la reconciliación, se ejecuta `_emit_and_enqueue()`, recalculando todos los techos dinámicos en `PortfolioOptimizer` y actualizando el dashboard en vivo de Rich y la cola de persistencia de Excel.

### Decisión 3: Sondeo periódico y despertar reactivo
- **Enfoque**:
  - Un helper asíncrono `_check_and_reload_excel()` comprueba periódicamente (cada 2.0 s o antes de cada ronda de patrulla) si el archivo fue modificado.
  - En `run_until_complete()`, la espera se articula con un `asyncio.Event` de recarga (`self._reload_event`) combinado con `self._stop_event`, permitiendo interrumpir la espera de patrulla si el usuario añade un lote nuevo.

### Decisión 4: Tolerancia a bloqueos de Windows
- Durante la lectura de inputs, cualquier excepción `PermissionError` u `OSError` (típica cuando Microsoft Excel está guardando en disco) se captura silenciosamente como incidencia transitoria, posponiendo la reconciliación al siguiente tick (2 s después) sin interrumpir el motor de pujas.

## Risks / Trade-offs

- **[Riesgo] Usuario introduce datos sintácticamente incorrectos a mitad de edición**:
  - *Mitigación*: `read_workbook_config()` valida que la URL sea válida y la puja máxima sea numérica y $>0$. Filas incompletas se marcan con error de validación o se ignoran temporalmente sin corromper el resto de relojes válidos.
- **[Riesgo] Reducción de presupuesto global por debajo del importe ya gastado o comprometido**:
  - *Mitigación*: `BudgetLedger.available_eur` utiliza `max(0, global - spent - committed)`. Si el nuevo presupuesto es inferior, el disponible pasa a 0 €, impidiendo nuevas pujas hasta que se liberen compromisos o se aumente el saldo.
