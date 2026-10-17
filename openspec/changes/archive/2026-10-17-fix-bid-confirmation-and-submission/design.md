# Design

## Context

Véase [proposal.md](file:///c:/Users/fgabr/Dev/ChronoBid/openspec/changes/fix-bid-confirmation-and-submission/proposal.md) para la motivación y el diagnóstico completo obtenido al inspeccionar en vivo el DOM de Catawiki por CDP:

1. **Modal de confirmación (`LotBidConfirmation.tsx`)**: Al pulsar un botón de puja rápida (`[data-testid="quick-bid-buttons"] button`) o el botón de puja directa, React/Floating UI monta de forma asíncrona un portal `<div data-floating-ui-portal="">` que contiene `<article role="dialog" data-sentry-component="LotBidConfirmation" data-sentry-source-file="LotBidConfirmation.tsx">`. Dentro de su `<footer>` se renderizan dos `<button data-sentry-source-file="LotBidConfirmation.tsx">` **sin atributo `data-testid`**:
   - Botón primario (`class="... c-button--primary ..."`): `"Confirmar puja"` (ES) / `"Confirm bid"` (EN).
   - Botón terciario (`class="... c-button--tertiary ..."`): `"Cancelar"` (ES) / `"Cancel"` (EN).
2. **Fila de puja manual (`BidInputRow.tsx`)**: Junto a `input[name="bid"]` existen dos botones sin `data-testid` ni `type="submit"`:
   - `button.c-button--secondary`: `"Pujar"` (ES) / `"Place bid"` (EN) — ejecuta una puja directa por el importe indicado.
   - `button.c-button--primary`: `"Fijar puja máx"` (ES) / `"Set max bid"` (EN) — configura una autopuja máxima en Catawiki (lo cual violaría la regla de invisibilidad del techo del usuario).
3. **Condición de carrera en `submit_minimum_bid`**: El método evaluaba `await conf_loc.count() > 0` inmediatamente (`0 ms`) después del clic inicial y ejecutaba `asyncio.sleep(self.confirmation_wait_seconds)` *después* del bucle de confirmación.
4. **Falso positivo de `SUPERADO` en `AuctionOrchestrator`**: `_run_assault_for_lot` invocaba `lot.record_own_bid(bid_amt)` de forma incondicional antes de verificar si `post_snap` reflejaba liderazgo o incremento de `current_bid_eur`.
5. **Persistencia de `is_opening_lot` en pestaña abierta**: En lotes que abrían con 0 pujas (`ssr_snap.current_bid_eur == 0` y `has_no_bids=True` en `<script id="__NEXT_DATA__">`), `extract_snapshot` seguía considerando `is_opening_lot = True` tras pujar en la misma pestaña sin recargar, sobreescribiendo `current_bid = 0`.

## Goals / Non-Goals

**Goals:**
- Garantizar que `CatawikiLotPage.submit_minimum_bid` detecte y pulse de forma fiable el botón principal de confirmación en el diálogo modal `LotBidConfirmation` de Catawiki (tanto en español como en inglés), esperando activamente su montaje en el DOM.
- Asegurar que cuando se utilice el campo numérico `input[name="bid"]`, se pulse exclusivamente el botón de puja directa (`"Pujar"` / `"Place bid"`, `c-button--secondary`) dentro de `BidInputRow`, nunca `"Fijar puja máx"`.
- Evitar bloqueos si un diálogo de confirmación previo hubiera quedado abierto y evitar falsos registros de puja (`SUPERADO`) en `AuctionOrchestrator` cuando una puja no llegue a confirmarse.
- Permitir que `extract_snapshot` refleje inmediatamente el nuevo importe y liderazgo tras la primera puja en un lote que abrió inicialmente con 0 pujas.

**Non-Goals:**
- No modificar la lógica matemática de `PortfolioOptimizer` ni las reservas de `BudgetLedger`.
- No utilizar el sistema de autopuja máxima de Catawiki (`"Fijar puja máx"`).

## Decisions

### Decisión 1: Selectores reales jerárquicos en `CatawikiSelectors`
- **`bid_confirm_selectors`**:
  1. `'article[role="dialog"][data-sentry-component="LotBidConfirmation"] button.c-button--primary'`
  2. `'button[data-sentry-source-file*="LotBidConfirmation"].c-button--primary'`
  3. `'article[role="dialog"] footer button.c-button--primary'`
  4. `'[role="dialog"] button:has-text("Confirmar puja")'`
  5. `'[role="dialog"] button:has-text("Confirm bid")'`
  6. `'button[data-testid="confirm-bid-button"]'` *(compatibilidad con fixtures de test)*
  7. `'button[data-testid="modal-confirm-bid"]'` *(compatibilidad con fixtures de test)*
- **`bid_submit_selectors`**:
  1. `'[data-sentry-component="BidInputRow"] button.c-button--secondary'`
  2. `'form:has(input[name="bid"]) button.c-button--secondary'`
  3. `'[data-sentry-component="BidInputRow"] button:has-text("Pujar")'`
  4. `'[data-sentry-component="BidInputRow"] button:has-text("Place bid")'`
  5. `'button[data-testid="place-bid-button"]'` *(compatibilidad con fixtures de test)*
  6. `'button[data-testid="bid-submit-button"]'` *(compatibilidad con fixtures de test)*
  - Se elimina `'button[type="submit"]'` genérico o se restringe a `'form:has(input[name="bid"]) button[type="submit"]'` para impedir clics accidentales en el buscador de la cabecera de Catawiki.

*Alternativas consideradas*:
- *Confiar únicamente en texto (`:has-text("Confirmar puja")`) sin selectores estructurales*: Descartado como única vía porque depende del idioma de la interfaz; combinar el atributo estructural `data-sentry-component="LotBidConfirmation"` + clase primaria `.c-button--primary` con los selectores de texto ES/EN ofrece resiliencia tanto a cambios de idioma como a cambios de bundlers.

### Decisión 2: Espera activa de aparición del modal de confirmación en `submit_minimum_bid`
- Añadir un parámetro configurable `confirm_appear_timeout_seconds: float = 2.5` (con intervalo de sondeo `confirm_poll_interval_seconds: float = 0.05`) en `CatawikiLotPage`.
- Flujo de `submit_minimum_bid(bid_amount_eur)`:
  1. Si antes de iniciar el clic ya existe un botón de confirmación visible de un diálogo `LotBidConfirmation` colgado de un tick anterior (o botón de cancelar), limpiarlo o gestionarlo para que su overlay no bloquee el clic.
  2. Ejecutar Paso 1 (clic en botón rápido `[data-testid="quick-bid-buttons"] button` cuyo importe coincida con `bid_amount_eur`) o Paso 2 (rellenar `input[name="bid"]` y pulsar el primer selector coincidente de `bid_submit_selectors`).
  3. **Bucle de espera activa de confirmación**: Sondear `self.selectors.bid_confirm_selectors` durante hasta `confirm_appear_timeout_seconds` (en incrementos de `0.05s`, realizando al menos una pasada inmediata para que con `confirmation_wait_seconds=0.0` en tests síncronos/simulados funcione al instante). En cuanto uno de los selectores tenga `count() > 0`, hacer clic en él y salir del bucle.
  4. Esperar `self.confirmation_wait_seconds` (por defecto `0.45s`) tras el clic de confirmación para que la petición de puja se complete y el DOM/red se actualicen, y devolver `await self.extract_snapshot()`.

### Decisión 3: Verificación de efectividad antes de `record_own_bid` en `AuctionOrchestrator`
- En `AuctionOrchestrator._run_assault_for_lot`:
  - Tras `post_snap = await controller.submit_minimum_bid(bid_amt)`, comprobar si la puja tuvo efecto real en el mercado:
    `bid_accepted = post_snap.is_leader or post_snap.current_bid_eur >= bid_amt`
  - Si `bid_accepted` es `True`: llamar a `lot.record_own_bid(bid_amt, reserve_status=post_snap.reserve_status)` y luego `lot.apply_market_snapshot(...)`. Si `not lot.is_leader` (otro postor nos superó al instante por un importe mayor o igual), liberar el compromiso con `await self.ledger.release(lot.lot_id)`.
  - Si `bid_accepted` es `False`: **no** llamar a `lot.record_own_bid(bid_amt)` (evitando ensuciar `my_last_bid_eur` y evitando el falso estado `SUPERADO`), liberar inmediatamente la reserva en `self.ledger.release(lot.lot_id)`, aplicar `lot.apply_market_snapshot(...)` y registrar una nota diagnóstica indicando que la puja no fue confirmada por Catawiki.

### Decisión 4: Desanclar `is_opening_lot` del `__NEXT_DATA__` inicial cuando el DOM en vivo ya muestra pujas
- En `CatawikiLotPage.extract_snapshot`:
  - `dom_has_no_bids = any(phrase in body_text for phrase in self.selectors.no_bids_phrases)`
  - `dom_is_leader = any(phrase in body_text for phrase in self.selectors.leader_phrases)`
  - Si `body_text` está presente (lectura DOM real de la página) y `not dom_has_no_bids` y (`dom_is_leader` o `(current_bid is not None and current_bid > 1)`), entonces el lote **ya no está en apertura sin pujas** (`is_opening_lot = False`), ignorando el flag estático `has_no_bids` de `ssr_snap` que quedó congelado desde la carga inicial de la pestaña.
  - Asimismo, si `dom_is_leader` es `True` y el DOM muestra `current_bid == 1`, es una puja real de `1 €` del propio usuario y no debe resetearse a `0 €`.

## Risks / Trade-offs

- **[Riesgo] Latencia ligeramente mayor al enviar puja si el modal tarda en aparecer** → Mitigación: El bucle de sondeo comprueba cada `50 ms` y sale inmediatamente en cuanto el botón `"Confirmar puja"` existe en el DOM (típicamente `< 150 ms`), sin añadir esperas fijas innecesarias.
- **[Riesgo] Doble bloque de puja en el DOM de Catawiki (layout responsive duplicado)** → Mitigación: Tanto los botones rápidos como el formulario `BidInputRow` están duplicados en el DOM pero ambos están conectados al mismo estado de React; hacer clic en la primera coincidencia visible dispara el portal único `<article role="dialog" data-sentry-component="LotBidConfirmation">`.
