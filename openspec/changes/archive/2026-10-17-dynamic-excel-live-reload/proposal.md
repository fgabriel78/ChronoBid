# Proposal

## Why

Actualmente, ChronoBid lee la configuración del fichero Excel (`relojes.xlsx`) una única vez en el arranque. Durante la ejecución, el agente escribe el estado hacia el archivo en segundo plano, pero nunca vuelve a inspeccionar el disco; si el usuario añade nuevos relojes, ajusta las pujas máximas, elimina filas o modifica el presupuesto global en caliente, la aplicación ignora por completo los cambios y permanece atada a la foto inicial.

Es necesario dotar al orquestador de capacidad de recarga y reconciliación dinámica en caliente, permitiendo al usuario modificar el Excel mientras la aplicación está corriendo y asegurando que las modificaciones (altas, bajas, cambios de tope, reactivación de lotes y presupuesto) se reflejen de inmediato en memoria y en las decisiones de puja.

## What Changes

- **Detección y lectura periódica de cambios en Excel**: Implementar un mecanismo de sondeo de modificación (`mtime`) y filtrado por firma de entradas (`InputSignature`: presupuesto `B1` y pares `URL`/`Puja Máxima` en columnas `A:B`) para detectar ediciones del usuario diferenciándolas de las escrituras de salida del worker.
- **Reconciliación en caliente del catálogo de relojes**:
  - Incorporar dinámicamente nuevos relojes añadidos al Excel y despertar de inmediato la ronda de patrulla para su primera lectura.
  - Actualizar la puja máxima (`max_bid_eur`) de los relojes existentes y revivir automáticamente a modo `PATRULLA`/`ASALTO` aquellos lotes descartados previamente si el nuevo tope supera la puja mínima de mercado.
  - Cancelar y remover lotes eliminados del Excel, deteniendo tareas de asalto activas y liberando compromisos de saldo si correspondiera.
  - Sincronizar el `row_index` cuando se inserten o reordenen filas en el Excel, preservando el estado en vivo de cada lote mediante su URL única.
- **Actualización dinámica del Presupuesto Global**:
  - Añadir método `update_global_budget` en `BudgetLedger` para ajustar el saldo global y recalcular de forma atómica el saldo disponible y los techos dinámicos de cartera ante cambios en la celda `B1`.
- **Resiliencia ante bloqueo de archivo en Windows**: Tolerar temporalmente excepciones `PermissionError` y `OSError` cuando el usuario esté guardando en Microsoft Excel sin detener la operativa ni generar fallos.

## Capabilities

### New Capabilities
<!-- No se introducen nuevas capacidades independientes. -->

### Modified Capabilities
- `excel-control`: Modificar los requisitos para exigir la detección periódica de cambios externos en el fichero Excel, la reconciliación atómica en memoria del catálogo y presupuesto global en caliente, y la reactivación de lotes viables tras incrementos de puja máxima.

## Impact

- **Código afectado**:
  - `src/chronobid/strategy/ledger.py`: soporte para actualización dinámica de presupuesto global (`update_global_budget`).
  - `src/chronobid/excel/workbook_io.py`: firma de entradas (`InputSignature`) y funciones de comparación de inputs.
  - `src/chronobid/orchestrator.py`: watcher/reconciliador en caliente `_check_and_reload_excel()`, actualización dinámica de `self.lots` y `self.ledger`, y evento de despertar reactivo.
- **Pruebas**: Nuevos tests unitarios y E2E simulando modificaciones dinámicas del Excel en disco durante la ejecución del orquestador.
