# Design: fix-catawiki-bid-extraction

## Context

ChronoBid se conecta a la instancia de navegador del usuario vía Chrome DevTools Protocol (CDP) con Playwright. Al consultar un lote, `CatawikiLotPage` extrae la instantánea del lote combinando selectores DOM y captura de respuestas de red. 

Catawiki es una aplicación Single Page Application construida sobre Next.js con Server-Side Rendering (SSR). Actualmente, la carga inicial incluye todo el estado de la subasta embebido en el DOM en `<script id="__NEXT_DATA__">`, mientras que los selectores CSS y los nombres de atributos para la puja actual y las acciones de puja han cambiado en la plataforma.

Véase [proposal.md](file:///c:/Users/fgabr/Dev/ChronoBid/openspec/changes/fix-catawiki-bid-extraction/proposal.md) para la motivación detallada.

## Goals / Non-Goals

**Goals:**
- Proporcionar lectura exacta y sin retraso de la puja actual (`current_bid_eur`), siguiente escalón mínimo (`next_min_bid_eur`), estado de reserva y tiempo de cierre.
- Implementar un extractor seguro de `__NEXT_DATA__` (`biddingBlockResponse`) evaluado en la página a través de Playwright.
- Actualizar `CatawikiSelectors` con los selectores y nombres de input vigentes en Catawiki (`[data-testid="lot-bid-status-current-bid"]`, `[data-testid="quick-bid-buttons"] button`, `input[name="bid"]`).
- Adaptar `parse_network_bidding_payload` para aceptar tanto las claves directas de Catawiki (`localizedCurrentBidAmount`, `localizedMinBidAmount`, `biddingEndTime`) como las anidadas (`live.lot.bid.EUR`).

**Non-Goals:**
- No alterar la lógica de cálculo de carteras ni contabilidad atómica de `BudgetLedger`.
- No alterar el motor de sniping en `TacticalBiddingEngine`.
- No realizar peticiones HTTP externas desacopladas del navegador del usuario (para evitar bloqueos anti-bot/Cloudflare).

## Decisions

### Decisión 1: Estrategia de extracción híbrida jerárquica
Extraer la información mediante una cascada priorizada:
1. **SSR / Hydration State (`__NEXT_DATA__`)**: Al navegar a un lote, se consulta el bloque `props.pageProps.biddingBlockResponse`. Proporciona de inmediato datos limpios y tipados: `localizedCurrentBidAmount`, `localizedMinBidAmount`, `reservePriceMet`, y `biddingEndTime` en milisegundos UTC.
2. **DOM Selectors (Actualizados)**: Si el script no existe o durante el seguimiento en vivo el DOM se actualiza mediante React, se leen los elementos DOM actualizados (`[data-testid="lot-bid-status-current-bid"]`).
3. **Network Interceptor**: Si llegan respuestas JSON de WebSocket o polling durante el seguimiento continuo en Asalto, se integran en `_last_network_snapshot`.
4. **Fusión inteligente de snapshot**: Si el valor leído en el DOM o en la red es mayor que el valor inicial de `__NEXT_DATA__`, prevalece el valor más reciente.

*Alternativas consideradas*:
- *Solo selectores DOM*: Muy sensible a cambios de clases o reestructuración de tags HTML.
- *Llamadas API directas*: Fallan con 403 Forbidden por la protección perimetral de Catawiki.

### Decisión 2: Actualización exhaustiva de selectores en `CatawikiSelectors`
- `current_bid_selectors`: Añadir `[data-testid="lot-bid-status-current-bid"]`, `[data-testid="lot-bid-status-box"] [data-testid="lot-bid-status-current-bid"]`.
- `next_min_bid_selectors`: Añadir `[data-testid="quick-bid-buttons"] button`, `input[name="bid"]`.
- `bid_input_selectors`: Añadir `input[name="bid"]`.
- `quick-bid` clicker en `submit_minimum_bid`: Buscar botones dentro de `[data-testid="quick-bid-buttons"]` además de `[data-testid="quick-bid-button"]`.

### Decisión 3: Normalización de campos en `parse_network_bidding_payload`
Mapear:
- Puja actual: `localizedCurrentBidAmount` -> `root.get("live", {}).get("lot", {}).get("bid", {}).get("EUR")` -> `current_bid_amount` -> `current_bid` -> `highest_bid_amount`.
- Escalón mínimo: `localizedMinBidAmount` -> `next_minimum_bid` -> `next_min_bid`.
- Tiempo: Si existe `biddingEndTime` (timestamp epoch en ms), derivar segundos restantes contra el reloj actual UTC, garantizando exactitud sub-segundo.

## Risks / Trade-offs

- **[Riesgo] Desfase temporal en `__NEXT_DATA__` si la pestaña permanece abierta**: `__NEXT_DATA__` contiene el estado en el momento de la carga inicial.
  - *Mitigación*: En Modo Asalto, el DOM visible se actualiza dinámicamente; `extract_snapshot` lee el DOM y la red, tomando siempre el valor máximo y más reciente entre `__NEXT_DATA__` y el DOM en vivo.
- **[Riesgo] Estructura nula de `pageProps` en URLs no estándar**:
  - *Mitigación*: Extracción protegida con `try-except` y encadenamiento seguro (`getattr`/`get`), cayendo limpiamente al parsing de DOM si falla.
