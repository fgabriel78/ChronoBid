# Tasks

## 1. Configuración del Proyecto y Modelos de Dominio

- [x] 1.1 Crear la estructura del proyecto Python (`pyproject.toml`, paquete `src/clock_agent/`, directorios `models/`, `excel/`, `strategy/`, `browser/`, `tests/`) y verificar que `pip install -e ".[dev]"` instala `playwright`, `openpyxl`, `rich`, `pytest` y `pytest-asyncio` sin errores.
- [x] 1.2 Implementar los modelos de dominio en `src/clock_agent/models/auction.py` (`WatchLot`, `AuctionStatus`, `ReserveStatus`, `GlobalBudgetState`, `BidDecision`) y verificar con `pytest tests/test_models.py` la validación de estados, invariantes de puja limpia y transiciones de subasta.

## 2. Gestor de Excel y Sincronización Resiliente (`excel-control`)

- [x] 2.1 Implementar el generador de plantilla y lector/escritor de `.xlsx` en `src/clock_agent/excel/workbook_io.py` (lectura de `B1` Presupuesto Global y columnas `A:B`, escritura de `B2:B4` y columnas `C:J` preservando las entradas del usuario) y verificar con `pytest tests/test_workbook_io.py` la lectura de filas válidas, detección de filas inválidas y actualización de celdas.
- [x] 2.2 Implementar el `ExcelSyncWorker` asíncrono con cola *write-behind* en `src/clock_agent/excel/sync_worker.py` que tolere `PermissionError` cuando el archivo está bloqueado en Windows y persista los cambios en cuanto se libere, verificándolo mediante tests con bloqueo simulado en `pytest tests/test_excel_sync.py`.

## 3. Contabilidad Atómica de Presupuesto y Optimizador de Cartera (`bidding-strategy-portfolio`)

- [x] 3.1 Implementar el `BudgetLedger` atómico (`asyncio.Lock`) en `src/clock_agent/strategy/ledger.py` para gestionar saldo global, gastado en firme, comprometido como líder provisional (con reserva diferencial y liberación instantánea al ser superado) y verificar con `pytest tests/test_ledger.py` que subastas concurrentes jamás exceden el presupuesto global.
- [x] 3.2 Implementar el `PortfolioOptimizer` en `src/clock_agent/strategy/optimizer.py` (poda en vivo de lotes con `SiguienteMínimo > PujaMáxima` y cálculo de Techo Dinámico $E_i$ basado en coste estimado y holgura $H_j$ para maximizar el número de relojes) y verificar con `pytest tests/test_optimizer.py` los escenarios de reserva para múltiples relojes baratos y liberación cuando pierden holgura.
- [x] 3.3 Implementar el motor de decisión táctica en `src/clock_agent/strategy/tactics.py` (invisibilidad hasta `< 25s`, puja por incremento mínimo, respuesta en prórrogas de `+90s` y lógica de escalera paso a paso para desbloquear el precio de reserva) y verificar todos los casos borde con `pytest tests/test_tactics.py`.

## 4. Motor de Navegador CDP e Interacción con Catawiki (`catawiki-browser-engine`)

- [x] 4.1 Implementar el conector CDP y verificador de sesión autenticada en `src/clock_agent/browser/cdp_client.py` (`connect_over_cdp` al puerto configurable `9222`), junto con un script/comando de ayuda para lanzar Chrome/Edge con depuración remota, verificando con `pytest tests/test_cdp_client.py` el manejo de conexión exitosa, caída de navegador y sesión no autenticada.
- [x] 4.2 Implementar el extractor de estado de lote y ejecutor de pujas en `src/clock_agent/browser/catawiki_page.py` (selectores configurables ES/EN, intercepción JSON/DOM para puja actual, siguiente incremento mínimo, segundos restantes, condición de líder, aviso de precio de reserva y envío de puja mínima) y verificar el parseo e interacción sobre páginas simuladas/fixtures con `pytest tests/test_catawiki_page.py`.

## 5. Orquestador Patrulla/Asalto, CLI y Verificación End-to-End

- [x] 5.1 Implementar el orquestador principal `AuctionOrchestrator` en `src/clock_agent/orchestrator.py` que coordine el Modo Patrulla (> 5 min con pestaña única) y el Modo Asalto (< 5 min y bucle +90s con pestañas dedicadas concurrentes), integrando `BudgetLedger`, `PortfolioOptimizer` y `ExcelSyncWorker`, y verificar con `pytest tests/test_orchestrator_e2e.py` una simulación completa de múltiples subastas solapadas con presupuesto compartido y precio de reserva.
- [x] 5.2 Implementar la interfaz de línea de comandos (`src/clock_agent/cli.py`) con panel en vivo `rich`, comando `--init-excel` para crear una plantilla `.xlsx` lista para usar y documentación de uso en `README.md`, verificando que `clock-agent --help` y `clock-agent --init-excel relojes.xlsx` se ejecutan correctamente.
