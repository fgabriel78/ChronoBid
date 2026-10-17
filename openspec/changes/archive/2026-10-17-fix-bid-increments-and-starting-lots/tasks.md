# Tasks: fix-bid-increments-and-starting-lots

## 1. Tabla de Escalones de Catawiki y Detección de Reserva

- [x] 1.1 Actualizar `compute_catawiki_next_min_bid` en `src/chronobid/browser/catawiki_page.py` con la tabla oficial de tramos de incremento de Catawiki (1 € - 10 €: +1 €, 11 € - 100 €: +5 €, 101 € - 200 €: +10 €, etc.) y verificar con pruebas unitarias parametrizadas en `tests/test_catawiki_page.py`.
- [x] 1.2 Refactorizar la detección de reserva en `extract_nextjs_ssr_snapshot` y `extract_snapshot` (`src/chronobid/browser/catawiki_page.py`) para ignorar `reservePriceMet` cuando sea `None`, comprobando frases explícitas de "sin precio de reserva" / "no reserve price" para asignar `ReserveStatus.NO_RESERVE`, y verificar con tests unitarios.

## 2. Soporte de Lotes Iniciales y Fusión Jerárquica

- [x] 2.1 Adaptar `extract_nextjs_ssr_snapshot` y `extract_snapshot` en `src/chronobid/browser/catawiki_page.py` para reconocer lotes sin pujas previas (`bids: []`, `localizedCurrentBidAmount: 0` o texto "No hay ninguna puja"), asignando la puja de apertura (`localizedStartBidAmount` o `quickBids[0]`, ej. 1 €) como siguiente escalón mínimo admisible.
- [x] 2.2 Relajar la condición de validación en `extract_snapshot` para aceptar `next_min` en lotes sin pujas previas sin forzar que deba ser estrictamente superior al importe de salida mostrado, y verificar que no se produzca descarte prematuro en `apply_market_snapshot` ni en `PortfolioOptimizer`.

## 3. Verificación de Regresión y Validación de Integración

- [x] 3.1 Añadir pruebas unitarias y de integración en `tests/test_catawiki_page.py` con el payload SSR y estructura DOM del lote Duward (apertura a 1 €, botones rápidos [1, 2, 3], sin precio de reserva), comprobando que el snapshot y la transición de estado mantienen el lote viable en `PATRULLA`.
- [x] 3.2 Ejecutar la suite completa de tests de ChronoBid con `pytest` y confirmar que todos los tests unitarios y de integración pasan satisfactoriamente sin regresiones.
