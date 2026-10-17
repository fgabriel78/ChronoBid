# Design

## Context

ChronoBid supervisa lotes de subastas en Catawiki mediante un bucle de patrulla periódico que crea una página en Playwright (vía CDP), navega a la URL del lote y extrae una instantánea tipada (`LotPageSnapshot`). Ver `proposal.md` para el detalle de cómo fallos temporales de carga en Catawiki devuelven páginas de error React/Next.js que provocaban la sobreescritura del título del reloj y el cierre prematuro e irreversible de la subasta con estado `PERDIDO`.

## Goals / Non-Goals

**Goals:**
- Detectar con fiabilidad pantallas de error transitorio de Catawiki (como *"We can't load this page right now"*) e impedir que corrompan el estado del lote.
- Desacoplar la ausencia de lectura de cuenta atrás (`None`) de la expiración de la subasta (`0.0`), impidiendo cierres ficticios.
- Garantizar que los fallos puntuales de red o servicio no alteren el estado activo (`PATRULLA`/`ASALTO`), el título legítimo ni la puja registrada en memoria o en el Excel.
- Ignorar títulos residuales de error al cargar el fichero Excel si este ya contenía mensajes como *"We can't load this page right now"*.

**Non-Goals:**
- No implementar sistemas de proxy rotativo ni reintentos agresivos inmediatos que aumenten el riesgo de detección por parte de Catawiki; los reintentos se canalizan de forma natural en el siguiente ciclo de patrulla programado.
- No modificar la lógica de cálculo de escalones ni la estrategia de pujas tácticas del optimizador de cartera.

## Decisions

### 1. Detección de páginas de error y snapshots inválidos en `CatawikiLotPage`
- **Decisión**: Añadir a `CatawikiSelectors` una tupla configurable de frases de error bilingües (`error_phrases`) que incluyan *"we can't load this page right now"*, *"no podemos cargar esta página"*, *"something went wrong"*, *"algo ha salido mal"*, códigos 4xx/5xx y avisos de Cloudflare.
- **Validación del snapshot**: Introducir una excepción `CatawikiPageLoadError` o atributo `is_error` / `is_valid` en `LotPageSnapshot`. Si el cuerpo de la página contiene alguna frase de error o no se detecta ningún contenedor genuino de subasta (`[data-testid="lot-title"]`, botones de puja o datos SSR), clasificar la lectura como fallo transitorio de carga en lugar de producir un snapshot normal con ceros.
- **Alternativa descartada**: Confiar en códigos HTTP de respuesta. Playwright / Next.js a menudo sirven páginas de error 200 OK con renderizado en el cliente ante fallos internos de API, por lo que la inspección semántica del DOM es indispensable.

### 2. Tratamiento de `seconds_remaining` ausente vs agotado
- **Decisión**: En `CatawikiLotPage.extract_snapshot`, si `seconds_rem` es `None` y no se detecta ninguna frase de cierre (`is_closed=False`), el snapshot debe mantener `seconds_remaining = None`. Únicamente se asignará `0.0` si `is_closed` es verdaderamente `True`.
- **Alternativa descartada**: Asignar un valor por defecto ficticio elevado (ej. 99999 segundos). Es propenso a errores en el modo asalto y oscurece la telemetría real.

### 3. Blindaje en `WatchLot.apply_market_snapshot`
- **Decisión**:
  - `self.is_closed = True` solo debe activarse si `is_closed` viene como `True` de forma explícita, o si `seconds_remaining` es numérico (`not None`) y menor o igual a 0 habiendo superado la hora de cierre prevista.
  - Si el lote ya tenía una fecha de cierre (`closing_time`) en el futuro (> 60 segundos desde ahora) y no hay frase explícita de subasta cerrada, no transicionar a estados terminales por lecturas anómalas.
  - Rechazar actualizaciones de `self.title` si el nuevo valor coincide con una frase de error reconocida.

### 4. Aislamiento y resiliencia en el Orquestador (`orchestrator.py`)
- **Decisión**: En `run_patrol_step` y `_run_assault_for_lot`, encapsular la extracción de cada lote en un bloque `try...except (CatawikiPageLoadError, Exception)`. Ante un fallo de carga:
  - Mantener intactos los campos de mercado existentes (`title`, `current_bid_eur`, `seconds_remaining`).
  - No llamar a `apply_market_snapshot` con valores residuales.
  - Anotar en `lot.notes`: `"Error puntual de carga en Catawiki (reintentando en la siguiente patrulla)"`.
  - Mantener el lote en su estado activo actual (`AuctionStatus.PATROL` o `AuctionStatus.ASSAULT`).

### 5. Saneamiento en la lectura de Excel (`workbook_io.py`)
- **Decisión**: Al leer `relojes.xlsx` en `read_workbook_config()`, si el valor de la columna 3 (*Título del Reloj*) coincide con frases de error de carga, inicializar `existing_title = ""` para que se refresque automáticamente con el título real en cuanto la página de Catawiki responda con normalidad.

## Risks / Trade-offs

- **[Riesgo]** Una subasta real finaliza durante una caída continuada de la red y el sistema tarda en marcarla como cerrada.
  → **Mitigación**: Si la fecha `closing_time` ya ha pasado con un margen de seguridad razonable y tras restablecerse la conexión se confirma el cierre, la subasta se liquida en el siguiente tick sin comprometer fondos adicionales.
- **[Riesgo]** Catawiki cambia la estructura o textos de su pantalla de error.
  → **Mitigación**: Se combinan selectores estrictos de título (`[data-testid="lot-title"]`) con la detección obligatoria de contenedores de subasta válidos (botones de puja o SSR). Si la página no contiene datos de subasta, no se considera un snapshot válido aunque el texto de error cambie.
