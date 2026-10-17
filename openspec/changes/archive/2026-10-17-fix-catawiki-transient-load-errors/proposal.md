# Proposal

## Why

Cuando Catawiki sufre una degradación temporal de servicio, lentitud de red o devuelve una pantalla de error (por ejemplo *"We can't load this page right now"* o códigos HTTP 5xx), el extractor de páginas de ChronoBid captura el encabezado `<h1>` de error como título del reloj y asume erróneamente que la cuenta atrás ha llegado a cero (`seconds_remaining = 0.0`). Esto provoca que el modelo marque la subasta prematuramente como `PERDIDO`, cerrándola de forma definitiva y descartándola para siempre de la cartera de seguimiento en el Excel, aun cuando la subasta real continúe abierta y falten días para su cierre.

Esta propuesta introduce resiliencia ante errores transitorios de carga en Catawiki, protegiendo la integridad del catálogo de relojes activos y evitando descalificaciones erróneas.

## What Changes

- **Detección de páginas de error y validación de snapshots**: Identificar respuestas de error de Catawiki (pantallas Next.js/React con *"We can't load this page right now"*, fallos HTTP 5xx o páginas sin los contenedores legítimos de subasta) e invalidar la extracción clasificándola como error transitorio de carga en lugar de una lectura de mercado válida.
- **Protección del título del lote**: Filtrar encabezados y selectores genéricos para que frases de error o avisos técnicos de la plataforma nunca sustituyan el título auténtico del reloj en memoria ni en el archivo Excel.
- **Distinción entre temporizador agotado y ausencia de temporizador**: Devolver `seconds_remaining = None` cuando no sea posible leer la cuenta atrás en una página abierta, evitando forzarlo a `0.0` y eliminando la falsa condición de cierre por tiempo expirado.
- **Resiliencia en el ciclo de patrulla y asalto**: Ante un fallo temporal de carga en un lote, mantener el lote en estado activo (`PATRULLA` / `ASALTO`) con sus datos previos intactos (título, puja actual, hora de cierre prevista), registrar una nota informativa de reintento en segundo plano y volver a consultar en la siguiente ronda.
- **Recuperación y saneamiento de datos en el archivo Excel**: Evitar que errores puntuales de lectura sobreescriban títulos o cambien estados activos a estados terminales (`PERDIDO`) en el fichero `.xlsx`.

## Capabilities

### New Capabilities
<!-- Ninguna nueva capacidad; se refina el comportamiento de las capacidades existentes -->

### Modified Capabilities
- `catawiki-browser-engine`: Resiliencia en la extracción de snapshots, detección y clasificación de páginas de error transitorias, y preservación del valor nulo de cuenta atrás cuando no está presente.
- `excel-control`: Preservación de títulos legítimos y estados activos ante fallos transitorios de monitorización, registrando advertencias de reintento sin transicionar a estados terminales en el reporte Excel.

## Impact

- `src/chronobid/browser/catawiki_page.py`: Selectores de título más estrictos, detección de errores de carga de Catawiki, preservación de `None` en `seconds_remaining`.
- `src/chronobid/models/auction.py`: Regla de cierre estricta que no asuma finalización si el tiempo restante es nulo o si la fecha de cierre previa está en el futuro y no hay indicador explícito de cierre.
- `src/chronobid/orchestrator.py`: Captura y gestión resiliente de snapshots defectuosos o excepciones en `run_patrol_step` y `_run_assault_for_lot`, protegiendo el ciclo de vida del lote.
- `tests/test_catawiki_page.py`, `tests/test_orchestrator_e2e.py`: Nuevas pruebas para simular páginas de error transitorio ("We can't load this page right now") y validar que no alteran el estado del lote.
