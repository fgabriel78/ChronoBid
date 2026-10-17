# Design: Corrección de Escalones de Puja, Apertura Inicial y Estado de Reserva

## Context

Véase `proposal.md` para la motivación del cambio. La extracción en `CatawikiLotPage` utilizaba una aproximación simplista en `compute_catawiki_next_min_bid` (asumiendo +5 € para todo valor < 50 €) e interpretaba `reservePriceMet: null` como reserva no alcanzada. Además, en lotes sin pujas previas (donde la puja actual es 0 € o se muestra 1 € como salida), la condición `next_min > cur_val` fallaba y calculaba un incremento indebido sobre la salida.

## Goals / Non-Goals

**Goals:**
- Actualizar `compute_catawiki_next_min_bid` a la tabla oficial de tramos de incremento de Catawiki (soportando escalones de +1 € para importes entre 1 € y 10 €).
- Garantizar que los lotes en estado de inicio ("EMPEZAR A PUJAR", 0 pujas en historial) mantengan como siguiente mínimo la puja de apertura (1 €) sin ser descartados prematuramente.
- Corregir la clasificación del estado de reserva para catalogar fielmente las subastas sin reserva (`ReserveStatus.NO_RESERVE`).

**Non-Goals:**
- Modificar el motor de optimización de cartera (`PortfolioOptimizer`) o la contabilidad del libro mayor (`BudgetLedger`), cuyas reglas de poda y asignación presupuestaria son matemáticas y ya están probadas.
- Soportar divisas distintas al Euro (€).

## Decisions

### 1. Tabla exhaustiva de tramos de incremento en `compute_catawiki_next_min_bid`
- **Decisión**: Implementar la tabla oficial de tramos de Catawiki:
  - `< 1 €`: 1 €
  - `1 € – 10 €`: +1 €
  - `11 € – 100 €`: +5 €
  - `101 € – 200 €`: +10 €
  - `201 € – 500 €`: +20 €
  - `501 € – 1.000 €`: +50 €
  - `1.001 € – 2.000 €`: +100 €
  - `2.001 € – 5.000 €`: +200 €
  - `5.001 € – 10.000 €`: +500 €
  - `> 10.000 €`: +1.000 €
- **Alternativa descartada**: Mantener escalones fijos de 5 €; descartada porque viola las reglas de la plataforma y provoca falsos descartes en lotes asequibles.

### 2. Tratamiento unificado de lotes sin pujas previas
- **Decisión**: En `extract_nextjs_ssr_snapshot` y `extract_snapshot`:
  - Si el historial de pujas está vacío (`bids: []`) o la página indica ausencia de pujas ("No hay ninguna puja", "EMPEZAR A PUJAR"), la puja actual se normaliza a `0 €` (o se preserva el importe de salida) y el siguiente mínimo permitido es la puja inicial (`localizedStartBidAmount` o `quickBids[0]`, típicamente 1 €).
  - La condición para aceptar `next_min` no exigirá `next_min > cur_val` cuando `cur_val == 0` o cuando no existan pujas registradas.
- **Alternativa descartada**: Exigir siempre `next_min > cur_val`; descartada porque cuando un lote sale a 1 €, la primera puja válida es 1 €, no 2 € ni 6 €.

### 3. Detección jerárquica de Precio de Reserva
- **Decisión**:
  - Si el título o el cuerpo contiene frases como `"sin precio de reserva"` o `"no reserve price"`, o si `has_reserve_price` / `hasReservePrice` en el lote es explícitamente `False`, asignar inmediatamente `ReserveStatus.NO_RESERVE`.
  - Solo evaluar `reservePriceMet` si no es `None` y el lote realmente cuenta con reserva (`hasReservePrice is not False`).
- **Alternativa descartada**: Confiar ciegamente en `if "reservePriceMet" in root`; descartada porque Catawiki envía `"reservePriceMet": null` para lotes sin reserva, y `bool(None)` es `False`.

## Risks / Trade-offs

- **[Riesgo]** Catawiki podría alterar ligeramente los botones rápidos en experimentos A/B.
  - *Mitigación*: Se preserva la lectura jerárquica: los botones y el campo de entrada del DOM en vivo prevalecen sobre el cálculo supletorio de `compute_catawiki_next_min_bid`.
