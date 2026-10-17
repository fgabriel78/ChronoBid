# Proposal

## Why

Al ejecutar una puja en Catawiki (ya sea mediante un botón de puja rápida o mediante el campo numérico), la plataforma despliega un diálogo modal de confirmación (`LotBidConfirmation.tsx`, `<article role="dialog">`) cuyo botón principal (`"Confirmar puja"` / `"Confirm bid"`) carece de atributo `data-testid`. Actualmente, `CatawikiLotPage.submit_minimum_bid` comprueba de forma instantánea (`0 ms`, sin espera activa) dos selectores `data-testid` inexistentes en el DOM real, por lo que nunca pulsa aceptar en la ventana de confirmación; además, `AuctionOrchestrator` registra incondicionalmente la puja como enviada (`record_own_bid`), provocando un falso estado `SUPERADO` y dejando el modal abierto bloqueando los siguientes intentos en Modo Asalto.

## What Changes

- Actualizar `CatawikiSelectors.bid_confirm_selectors` y `CatawikiSelectors.bid_submit_selectors` con los selectores reales del DOM de Catawiki inspeccionados en vivo (`article[role="dialog"][data-sentry-component="LotBidConfirmation"] button.c-button--primary`, `button[data-sentry-source-file*="LotBidConfirmation"].c-button--primary`, `[data-sentry-component="BidInputRow"] button.c-button--secondary` y selectores bilingües ES/EN por texto), seleccionando siempre el botón de puja directa (`"Pujar"` / `"Place bid"`) y nunca el de autopuja máxima (`"Fijar puja máx"`).
- Implementar espera activa en `CatawikiLotPage.submit_minimum_bid` tras el clic inicial para detectar la aparición del diálogo modal de confirmación, pulsar el botón de confirmación (o confirmar un diálogo previo que hubiera quedado abierto) y aguardar al cierre del modal y refresco del DOM/red antes de extraer el nuevo snapshot.
- Condicionar `lot.record_own_bid(bid_amt)` en `AuctionOrchestrator._run_assault_for_lot` a que el snapshot posterior confirme que la puja ha sido procesada (`post_snap.is_leader` o `post_snap.current_bid_eur >= bid_amt`), liberando el saldo reservado y evitando transiciones falsas a `SUPERADO` si la confirmación no se completó.
- Corregir la evaluación de `is_opening_lot` en `CatawikiLotPage.extract_snapshot` para que, una vez emitida una puja en una pestaña que abrió inicialmente sin pujas (`__NEXT_DATA__` con `current_bid_eur == 0`), el estado SSR estático inicial no sobrescriba a `0 €` la nueva puja reflejada en el DOM en vivo.

## Capabilities

### New Capabilities

### Modified Capabilities
- `catawiki-browser-engine`: Refinar el envío y confirmación modal de pujas por incremento mínimo (espera activa del diálogo de confirmación, selectores reales de envío/confirmación en Catawiki y verificación de aceptación de la puja) así como la actualización en vivo tras la primera puja en lotes de apertura inicial.

## Impact

- `src/chronobid/browser/catawiki_page.py`: `CatawikiSelectors`, `CatawikiLotPage.submit_minimum_bid` y `CatawikiLotPage.extract_snapshot`.
- `src/chronobid/orchestrator.py`: Verificación post-puja en `AuctionOrchestrator._run_assault_for_lot`.
- `tests/test_catawiki_page.py` y `tests/test_orchestrator_e2e.py`: Nuevos tests para el diálogo real `LotBidConfirmation`, formulario `BidInputRow`, espera asíncrona de aparición del modal, rechazo de falsos positivos cuando una puja no se confirma y transición de lote sin pujas a lote con puja confirmada.
