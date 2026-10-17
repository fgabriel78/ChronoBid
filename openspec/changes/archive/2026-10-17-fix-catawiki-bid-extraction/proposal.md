# Proposal: fix-catawiki-bid-extraction

## Why

Al ejecutar la aplicación por primera vez, la columna "Actual" muestra 0 € en todos los lotes vigilados (incluso con pujas activas como 260 € en el Seiko Prospex). Esto ocurre porque los selectores DOM de puja actual en `CatawikiSelectors` están desactualizados (`[data-testid="current-bid-amount"]` en lugar del actual `[data-testid="lot-bid-status-current-bid"]`) y no se aprovecha el objeto de estado SSR inyectado por Catawiki en Next.js (`__NEXT_DATA__`), lo que deja el importe actual en `None` (0 € por defecto) e impide calcular el escalón mínimo y tomar decisiones tácticas correctas.

## What Changes

- **Extracción de estado híbrida (Next.js SSR + DOM)**: Extraer de forma primaria los datos del bloque de subasta (`biddingBlockResponse` en `__NEXT_DATA__`), obteniendo con precisión atómica el importe actual (`localizedCurrentBidAmount`), el siguiente escalón mínimo (`localizedMinBidAmount`), el estado de reserva (`reservePriceMet`), el estado de cierre y la hora de fin en milisegundos (`biddingEndTime`).
- **Actualización de selectores DOM de Catawiki**:
  - Actualizar `current_bid_selectors` incorporando `[data-testid="lot-bid-status-current-bid"]`.
  - Actualizar `next_min_bid_selectors` y `bid_input_selectors` incorporando `input[name="bid"]`.
  - Actualizar `next_min_bid_selectors` y botones rápidos para soportar el contenedor `[data-testid="quick-bid-buttons"]` y sus botones hijos.
- **Normalización en `parse_network_bidding_payload`**: Soportar las claves utilizadas por Catawiki en sus respuestas y scripts (`localizedCurrentBidAmount`, `localizedMinBidAmount`, `biddingEndTime`, `live.lot.bid.EUR`).

## Capabilities

### New Capabilities
<!-- Ninguna nueva capacidad; se refina la capacidad del motor de navegador existente. -->

### Modified Capabilities
- `catawiki-browser-engine`: Actualizar el escenario de lectura de estado del lote para garantizar la extracción precisa de la puja actual y escalón mínimo mediante la combinación de SSR (`__NEXT_DATA__`) y los selectores DOM actualizados de Catawiki.

## Impact

- **Código afectado**:
  - `src/chronobid/browser/catawiki_page.py`: Selectores `CatawikiSelectors`, métodos `extract_snapshot` y `parse_network_bidding_payload`, y extractor auxiliar de `__NEXT_DATA__`.
- **Tests**:
  - `tests/test_catawiki_page.py`: Pruebas unitarias de parsing con HTML real de Catawiki y fixtures JSON con `localizedCurrentBidAmount`.
- **Impacto en el sistema**: Corrige la columna "Actual" en la UI de consola, la hoja Excel y la evaluación de techos dinámicos en `AuctionOrchestrator`.
