# Proposal: Corregir Escalones de Puja Mínima, Lotes Iniciales y Detección de Reserva

## Why

Actualmente, el sistema calcula el siguiente escalón mínimo de puja aplicando un incremento de +5 € para cualquier puja menor a 50 €, descartando erróneamente subastas activas de bajo importe (por ejemplo, lotes con puja actual de 1 € y máximo de 5 € quedan descartados con la nota `siguiente mínimo (6 €) > máximo (5 €)` cuando en Catawiki el siguiente mínimo real es 2 € o 1 €). Además, en lotes sin pujas previas o sin precio de reserva ("Sin precio de reserva"), el estado se interpreta erróneamente como reserva no alcanzada o se fuerza un recálculo indebido del siguiente mínimo.

## What Changes

- **Alineación con la tabla oficial de escalones de Catawiki**: Actualizar `compute_catawiki_next_min_bid` para reflejar con exactitud los tramos oficiales de la plataforma (1 € – 10 €: +1 €; 11 € – 100 €: +5 €; 101 € – 200 €: +10 €; 201 € – 500 €: +20 €; 501 € – 1000 €: +50 €, etc.).
- **Soporte de lotes iniciales sin pujas previas**: Reconocer cuando un lote está en fase de inicio ("EMPEZAR A PUJAR", `bids: []`, `localizedCurrentBidAmount: 0`), adoptando la puja de salida (`localizedStartBidAmount` o `localizedMinBidAmount`, habitualmente 1 €) como siguiente puja mínima válida sin exigir que supere estrictamente a una puja actual ficticia.
- **Detección robusta de subastas Sin Precio de Reserva**: Corregir la lectura de `reservePriceMet` en `__NEXT_DATA__` para que valores `null` o inexistentes no se categoricen como `NOT_MET`, integrando la detección de las frases explícitas de Catawiki ("sin precio de reserva", "no reserve price") para asignar `ReserveStatus.NO_RESERVE`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `catawiki-browser-engine`: Precisión en la extracción del siguiente escalón mínimo de puja según la tabla de tramos de Catawiki, tratamiento de lotes en apertura inicial (sin pujas) y clasificación fidedigna de lotes sin precio de reserva.

## Impact

- **Código afectado**:
  - `src/chronobid/browser/catawiki_page.py` (`compute_catawiki_next_min_bid`, `extract_nextjs_ssr_snapshot`, `extract_snapshot`, `CatawikiSelectors`).
- **Pruebas y Validación**:
  - `tests/test_catawiki_page.py` incorporando casos de prueba para tramos de micro-puja (1-10 €), lotes con 0 pujas y lotes con etiqueta "Sin precio de reserva".
  - Suite de integración completa con `pytest`.
