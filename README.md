# ChronoBid

Agente autónomo de pujas de relojes en **Catawiki** controlado mediante un fichero **Excel (`.xlsx`)** en segundo plano, con contabilidad atómica de presupuesto en euros, optimizador dinámico de cartera para maximizar el número de relojes adjudicados y conexión vía **Chrome DevTools Protocol (CDP)** sobre la sesión autenticada del usuario.

---

## Características Principales

1. **Control y Reporte en Segundo Plano por Excel (`.xlsx`)**:
   - Define en la celda `B1` tu **Presupuesto Global (EUR)** y en las columnas `A` y `B` (desde la fila 7) la **URL de Catawiki** y tu **Puja Máxima limpia (EUR)** por cada reloj.
   - `ExcelSyncWorker` actualiza automáticamente el presupuesto gastado (`B2`), comprometido como líder (`B3`), disponible (`B4`) y las columnas de estado (`C:J`) tolerando bloqueos (`PermissionError`) cuando abres el archivo en Microsoft Excel.
2. **Conexión Transparente a Navegador Autenticado (CDP)**:
   - Se enlaza a tu instancia real de Chrome o Edge (`--remote-debugging-port=9222`), reutilizando tu sesión iniciada en Catawiki sin exponer contraseñas ni disparar bloqueos anti-bot.
3. **Dos Velocidades Operativas**:
   - **Modo Patrulla (> 5 min)**: Vigilancia periódica ligera y poda automática de relojes que superen tu máximo antes del cierre.
   - **Modo Asalto (≤ 5 min y prórrogas de +90s)**: Pestaña dedicada en tiempo real para cada lote en sus minutos finales.
4. **Tácticas de Asalto y Escalera de Precio de Reserva**:
   - **Invisibilidad hasta `< 25s`**: No revela tu interés ni tu tope máximo antes de los últimos 25 segundos.
   - **Puja por incremento mínimo**: Siempre puja el siguiente escalón mínimo exigido por Catawiki.
   - **Bucle de +90 segundos**: Contraataca durante las prórrogas mientras el siguiente escalón mínimo no supere el Techo Dinámico ($E_i$) ni el saldo disponible.
   - **Escalera de Precio de Reserva**: Si ya eres el máximo pujador en los últimos 25s pero persiste el aviso *"Precio de reserva no alcanzado"*, sube escalón a escalón y se detiene en el instante exacto en que desaparece dicho aviso.
5. **Optimizador Dinámico de Cartera (`PortfolioOptimizer`) y `BudgetLedger` Atómico**:
   - Garantiza mediante un `asyncio.Lock` que la suma de relojes ganados más las pujas donde eres líder provisional jamás exceda el presupuesto global.
   - Ajusta el Techo Dinámico ($E_i \le M_i$) reservando capital cuando una combinación de 2 o más relojes futuros baratos y con alta holgura permita ganar más relojes con el mismo presupuesto.

---

## Instalación

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

---

## Guía de Uso Rápido

### 1. Generar la plantilla Excel

```powershell
chronobid --init-excel relojes.xlsx --budget 1500
```

Abre `relojes.xlsx`, ajusta el **Presupuesto Global (EUR)** en la celda `B1` y añade en las filas a partir de la `7` la **URL del lote en Catawiki** (columna `A`) y tu **Puja Máxima limpia en euros** (columna `B`).

### 2. Abrir tu navegador con depuración remota (CDP) e iniciar sesión en Catawiki

Puedes obtener el comando exacto para Windows ejecutando:

```powershell
chronobid --print-browser-cmd chrome
```

O ejecutar directamente en **PowerShell** (nota el operador `&` al principio para rutas entre comillas):

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:LOCALAPPDATA\ChronoBid\chrome-profile" https://www.catawiki.com/es/
```

Inicia sesión normalmente en tu cuenta de Catawiki dentro de esa ventana del navegador.

### 3. Lanzar el agente

```powershell
chronobid --excel relojes.xlsx --cdp-port 9222
```

---

## Ejecución de Tests

```powershell
pytest -v
```
