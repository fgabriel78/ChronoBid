# Proposal

## Why

Participar manualmente en subastas de relojes en Catawiki requiere estar presente en el minuto exacto de cierre de múltiples lotes que a menudo se solapan y se extienden en bucles de +90 segundos, gestionando mentalmente precios máximos individuales, precios de reserva ocultos y un presupuesto global compartido. Un agente autónomo controlado mediante un fichero Excel permite delegar la monitorización y ejecución táctica en los segundos finales para maximizar el número de relojes adjudicados sin superar jamás el límite de cada lote ni el presupuesto total en euros.

## What Changes

- Creación del agente autónomo `ChronoBid` en Python 3.11+ (`asyncio` + `playwright` + `openpyxl` + `rich`) para la gestión integral de pujas de relojes en Catawiki.
- Implementación de un motor de control y reporte en segundo plano basado en un fichero Excel (`.xlsx`) con tolerancia a bloqueos de lectura/escritura cuando el usuario abre el archivo en Windows.
- Integración con Catawiki mediante conexión **Chrome DevTools Protocol (CDP)** sobre el navegador real ya autenticado del usuario, evitando bloqueos anti-bot (DataDome/Cloudflare) y operando en dos velocidades: *Modo Patrulla* (> 5 min para el cierre) y *Modo Asalto* (< 5 min y prórrogas de +90s).
- Implementación de un **Gestor Atómico de Presupuesto (Ledger)** en tiempo real (`Disponible = Global - Ganados - Comprometidos como líder actual`) y un **Optimizador Dinámico de Cartera** que ajusta el techo efectivo de cada lote activo para maximizar el número total de relojes ganados.
- Implementación de tácticas de asalto en los últimos segundos (< 25s): ocultación del precio máximo mediante pujas por incremento mínimo, respuesta en bucles de +90s y **Escalera de Desbloqueo de Precio de Reserva** paso a paso.

## Capabilities

### New Capabilities
- `excel-control`: Lectura de configuración inicial (Presupuesto Global en euros, URLs de Catawiki y Puja Máxima limpia por reloj) y actualización asíncrona resiliente del estado de cada subasta y contadores de presupuesto en el fichero `.xlsx`.
- `catawiki-browser-engine`: Conexión vía CDP a navegador autenticado, extracción de estado en tiempo real (puja actual, cuenta atrás, estado de líder, aviso de precio de reserva), ciclo de vida Patrulla/Asalto y ejecución de pujas incrementales y escalera de reserva.
- `bidding-strategy-portfolio`: Contabilidad atómica de presupuesto en vuelo, poda automática de lotes que superan su máximo y cálculo del Techo Dinámico (`Dynamic Ceiling`) para maximizar el número de relojes conseguidos dentro del presupuesto global.

### Modified Capabilities
<!-- No hay capacidades previas modificadas al tratarse de la creación inicial del proyecto ChronoBid. -->

## Impact

- **Nuevo código base en `ChronoBid`**: Estructura modular en Python (`src/chronobid/`) con paquetes dedicados a `excel/`, `browser/`, `strategy/`, `models/` y CLI de arranque.
- **Dependencias principales**: `playwright` (conexión CDP y automatización DOM/red), `openpyxl` (lectura y escritura de `.xlsx`), `rich` (telemetría en consola), `pytest` + `pytest-asyncio` (tests unitarios y de simulación de subastas).
- **Entorno de ejecución**: Windows con navegador basado en Chromium (Chrome, Edge o Brave) lanzado con `--remote-debugging-port=9222` y sesión iniciada en Catawiki.
