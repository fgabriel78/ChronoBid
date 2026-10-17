# Tasks

## 1. Detección y filtrado de errores en el navegador de Catawiki

- [x] 1.1 Definir la lista de frases de error bilingües (`error_phrases`) y la excepción `CatawikiPageLoadError` en `src/chronobid/browser/catawiki_page.py`, asegurando que `title_selectors` no capture encabezados de error como título del lote y verificar mediante test unitario en `tests/test_catawiki_page.py`.
- [x] 1.2 Actualizar `extract_snapshot` en `src/chronobid/browser/catawiki_page.py` para validar la existencia de contenedores legítimos de subasta (DOM o Next.js SSR), lanzar `CatawikiPageLoadError` ante pantallas de error ("We can't load this page right now", fallos 5xx, etc.) y preservar `seconds_remaining = None` cuando el temporizador no esté presente en subastas no cerradas, verificando con `pytest tests/test_catawiki_page.py`.

## 2. Blindaje del modelo de subasta y saneamiento de Excel

- [x] 2.1 Modificar `WatchLot.apply_market_snapshot` en `src/chronobid/models/auction.py` para no transicionar a `is_closed=True` ni a estado terminal `PERDIDO` si `seconds_remaining` es `None` o si `closing_time` es futuro y no hay indicador explícito de cierre, además de proteger `title` contra textos de error, verificando con `pytest tests/test_models.py`.
- [x] 2.2 Actualizar `read_workbook_config` en `src/chronobid/excel/workbook_io.py` para ignorar títulos previos que coincidan con frases de error al cargar el catálogo de relojes desde Excel, verificando con `pytest tests/test_workbook_io.py`.

## 3. Resiliencia en el Orquestador y verificación integral

- [x] 3.1 Proteger `run_patrol_step` y `_run_assault_for_lot` en `src/chronobid/orchestrator.py` capturando `CatawikiPageLoadError` y excepciones de navegación por cada lote individual, manteniendo el lote en estado activo (`PATRULLA`/`ASALTO`) con una nota de advertencia de reintento sin modificar sus valores de mercado ni cerrar el lote, y verificar con nuevos tests en `tests/test_orchestrator_e2e.py`.
- [x] 3.2 Sanear en el archivo `relojes.xlsx` la fila afectada del lote `107658893` limpiando el mensaje de error del título y restableciendo su estado a `PATRULLA` con puja previa intacta, y ejecutar la suite completa de pruebas con `pytest` para certificar la estabilidad general.
