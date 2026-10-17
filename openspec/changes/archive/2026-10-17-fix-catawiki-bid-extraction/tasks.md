# Tasks: fix-catawiki-bid-extraction

## 1. Actualización de Selectores y Normalización de Payloads

- [x] 1.1 Actualizar `CatawikiSelectors` en `src/chronobid/browser/catawiki_page.py` incorporando `[data-testid="lot-bid-status-current-bid"]`, `input[name="bid"]`, y `[data-testid="quick-bid-buttons"] button`, y verificar que los selectores están definidos.
- [x] 1.2 Extender `parse_network_bidding_payload` en `src/chronobid/browser/catawiki_page.py` para soportar las claves directas de Catawiki (`localizedCurrentBidAmount`, `localizedMinBidAmount`, `biddingEndTime`, `live.lot.bid.EUR`, `reservePriceMet`), y verificar con tests de unidad.

## 2. Extractor de Estado SSR e Integración Híbrida

- [x] 2.1 Implementar la lectura de `__NEXT_DATA__` (`biddingBlockResponse`) en `CatawikiLotPage.extract_snapshot` vía `page.evaluate`, convirtiéndolo en un `LotPageSnapshot` tipado.
- [x] 2.2 Integrar la fusión jerárquica en `extract_snapshot` (Next.js SSR -> DOM Selectors -> Network Interceptor), asegurando que si el DOM en vivo avanza con pujas superiores a la carga inicial, el snapshot mantenga el valor más reciente.
- [x] 2.3 Actualizar `submit_minimum_bid` para localizar botones rápidos dentro del contenedor `[data-testid="quick-bid-buttons"]` e interactuar con `input[name="bid"]`.

## 3. Pruebas de Regresión y Validación

- [x] 3.1 Crear pruebas unitarias en `tests/test_catawiki_page.py` con fragmentos HTML de lotes reales de Catawiki y payloads de `__NEXT_DATA__`, verificando la extracción de `260 €` de puja actual y `280 €` de escalón mínimo.
- [x] 3.2 Ejecutar la suite de tests completa con `pytest` y verificar que todos los tests unitarios y de integración de ChronoBid pasan satisfactoriamente.
