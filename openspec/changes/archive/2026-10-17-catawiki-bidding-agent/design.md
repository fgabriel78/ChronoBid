# Design

## Context

Ver `proposal.md` para la motivación del proyecto. `ClockAgent` debe operar sobre subastas reales de Catawiki desde una máquina Windows, tomando como única fuente de órdenes un archivo Excel local (`.xlsx`) y reportando en él la evolución de cada lote.

Restricciones técnicas clave del entorno:
1. **Ausencia de API pública y protección DataDome/Cloudflare**: Las peticiones HTTP directas fuera de un navegador real son bloqueadas por huella TLS (JA3/JA4) y telemetría de comportamiento.
2. **Regla de cierre de Catawiki (+90 segundos)**: Cualquier puja en los últimos 60 segundos reinicia/extiende la cuenta atrás en +90 segundos de forma indefinida. Además, los lotes de una misma subasta temática cierran en cascada con 1-2 minutos de diferencia, provocando solapamientos frecuentes.
3. **Bloqueo exclusivo de archivos `.xlsx` en Windows**: Cuando el usuario abre el fichero en Microsoft Excel para consultar el estado, el sistema operativo bloquea la escritura sobre el archivo en disco.

## Goals / Non-Goals

**Goals:**
- Conectar de forma transparente a una sesión real de navegador Chromium mediante Chrome DevTools Protocol (`CDP`) en el puerto local (`9222`).
- Garantizar mediante concurrencia cooperativa sobre un único bucle de eventos (`asyncio`) que el presupuesto comprometido + gastado jamás supere el presupuesto global en euros, incluso con múltiples pestañas en guerra de pujas simultánea.
- Maximizar el número total de relojes adjudicados mediante un motor heurístico de cartera que ajuste dinámicamente el techo de puja de cada lote en función de la viabilidad y coste de los lotes pendientes.
- Desacoplar el bucle crítico de pujas de la escritura en disco en el Excel mediante una cola *write-behind* en memoria resistente a `PermissionError`.

**Non-Goals:**
- Automatizar el login, resolución de CAPTCHAs o 2FA (el usuario deja la sesión ya iniciada en su navegador).
- Incluir en el cálculo del máximo la comisión de protección al comprador (9% + 3 €) o gastos de envío: los importes del Excel representan pujas limpias en Catawiki.
- Crear una interfaz gráfica de escritorio o web pesada: el panel de control del usuario es el propio fichero Excel acompañado de salida estructurada en terminal (`rich`).

## Decisions

### 1. Stack Tecnológico: Python 3.11+ (`asyncio` + `playwright` + `openpyxl` + `rich`)
- **Decisión**: Construir el agente en Python asíncrono monoprocreso (`asyncio`) utilizando `playwright.async_api` para el control del navegador por CDP y `openpyxl` para la persistencia en `.xlsx`.
- **Por qué**: En un bucle de eventos `asyncio` de un solo hilo con un `asyncio.Lock` en el `BudgetLedger`, la verificación de saldo disponible y la reserva previa al clic de puja son estrictamente atómicas sin riesgo de condiciones de carrera entre hilos.
- **Alternativas consideradas**:
  - *Peticiones HTTP directas / API scraping*: Descartado por bloqueo inmediato de DataDome/Cloudflare y riesgo de suspensión de cuenta.
  - *Automatización Excel vía COM (`xlwings`)*: Descartado porque el usuario prefiere que el agente trabaje con el Excel en segundo plano y abrirlo puntualmente cuando lo necesite, sin requerir que Microsoft Excel permanezca abierto.

### 2. Interacción Híbrida sobre Navegador Autenticado (CDP + Lectura DOM/Red)
- **Decisión**: Conectarse vía `playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")` al navegador del usuario y dividir el seguimiento en dos fases:
  - **Modo Patrulla (> 5 min para el cierre)**: Una única pestaña de fondo recorre secuencialmente las URLs con cadencia humanizada para actualizar horas de cierre, puja actual, incremento mínimo y estado de reserva.
  - **Modo Asalto (< 5 min para el cierre)**: Se abre/mantiene una pestaña dedicada por cada reloj viable que entra en sus últimos 5 minutos, interceptando respuestas de red / mutaciones del DOM cada ~250ms.

```
+-------------------------------------------------------------------------+
|               FLUJO DE EJECUCION EN MODO ASALTO (< 5 MIN)               |
+-------------------------------------------------------------------------+

  [Pestana CDP del Reloj i]
         |
         v (Tick cada 250ms / Evento de red)
  [Extrae: Puja_Actual, Siguiente_Minimo, Segundos_Restantes,
           Soy_Lider, Reserva_Alcanzada, Subasta_Cerrada]
         |
         +---> ¿Me han superado siendo antes Lider?
         |        +---> SI: Ledger.release(id_reloj)  [Libera saldo al instante]
         |
         +---> ¿Faltan <= 25s y (No soy Lider O Reserva NO alcanzada)?
                  |
                  v
         [PortfolioOptimizer calcula Techo_Dinamico E_i]
                  |
                  +---> ¿Siguiente_Minimo <= E_i?
                           |
                          SI ---> Ledger.try_reserve(id_reloj, Siguiente_Minimo)
                                         |
                                        OK ---> [Ejecuta Puja Minima en UI]
                                                [Encola actualizacion Excel]
```

### 3. Algoritmo del Optimizador Dinámico de Cartera (`PortfolioOptimizer`)
- **Decisión**: Calcular en cada decisión de puja un **Techo Dinámico Efectivo ($E_i$)** para el lote activo $i$ combinando su límite individual ($M_i$), el saldo disponible ($B_{\text{disp}}$) y el coste de oportunidad frente al resto de lotes viables pendientes ($J$).
- **Modelo matemático**:
  1. **Poda**: Todo lote $j$ donde $\text{SiguienteMínimo}_j > M_j$ pasa a `DESCARTADO`.
  2. **Coste estimado y Holgura de lotes futuros**: Para cada lote futuro viable $j \in J$:
     - Coste esperado conservador: $\hat{C}_j = \text{SiguienteMínimo}_j + \alpha \cdot (M_j - \text{SiguienteMínimo}_j)$ (con $\alpha \in [0.25, 0.40]$ para contemplar subida antes del cierre).
     - Índice de viabilidad (holgura): $H_j = \frac{M_j - \text{SiguienteMínimo}_j}{M_j}$. Solo los lotes con holgura suficiente ($H_j \ge H_{\text{min}}$, p. ej. $\ge 0.15$) compiten por reservar presupuesto frente a una compra inminente.
  3. **Maximización del número de relojes**:
     - Ordenando los lotes futuros de alta viabilidad de menor a mayor coste estimado ($\hat{C}_{(1)} \le \hat{C}_{(2)} \le \dots$), el optimizador busca si con el presupuesto disponible tras ganar el lote actual al precio $p$ ($B_{\text{disp}} - \Delta p$) se reduce el número máximo de relojes alcanzables en comparación con **no** subir hasta $p$ y reservar ese capital para adquirir **2 o más relojes futuros** ($\hat{C}_{(1)} + \hat{C}_{(2)} \le B_{\text{disp}}$).
     - Si mantener la puja del lote actual por debajo de un umbral permite sumar 2+ relojes futuros de alta holgura en lugar de 1 solo reloj caro, $E_i$ se limita a:
       $$E_i = \min\left(M_i,\; \text{MiPujaActual}_i + B_{\text{disp}} - \text{ReservaCartera}_i\right)$$
     - En cuanto los lotes futuros suben de precio y pierden su holgura ($H_j < H_{\text{min}}$), $\text{ReservaCartera}_i$ cae a $0$ y $E_i$ sube automáticamente hasta $\min(M_i, \text{MiPujaActual}_i + B_{\text{disp}})$.

### 4. Táctica de Escalera para el Precio de Reserva
- **Decisión**: Cuando `Soy_Lider == True`, `Segundos_Restantes <= 25` y `Reserva_Alcanzada == False`, el agente no introduce de golpe $E_i$, sino que envía **un único incremento mínimo**, espera la confirmación de actualización del DOM/red (~400-600ms) y comprueba si el indicador de "precio de reserva no alcanzado" ha desaparecido.
- **Por qué**: Evita sobrepagar por encima del precio de reserva oculto del vendedor y preserva el máximo presupuesto posible para los siguientes relojes de la lista.

### 5. Estructura del Fichero Excel y Cola *Write-Behind*
- **Decisión**: Mantener el estado autoritativo en memoria (`AuctionStateStore`) y sincronizar hacia el archivo `.xlsx` mediante un `ExcelSyncWorker` asíncrono:
  - Las escrituras se realizan primero sobre un archivo temporal en el mismo directorio y se reemplazan atómicamente (`os.replace`), o directamente sobre el destino si no hay bloqueo.
  - Si `os.replace` o `workbook.save` lanza `PermissionError` (porque el usuario tiene el Excel abierto para consultarlo), el worker marca el estado como `dirty`, registra un aviso no intrusivo en consola y reintenta cada 2 segundos hasta que el usuario cierre el archivo.
  - Además, en cada ciclo de patrulla (cuando el archivo no tiene cambios pendientes de volcar), el agente puede releer las celdas de entrada (`Presupuesto Global` y `Puja Máxima`) por si el usuario ha ajustado algún tope en caliente.

```
+----------------------------------------------------------------------------+
|                        ESTRUCTURA DE LA HOJA EXCEL                         |
+----------------------------------------------------------------------------+
| Celda B1: Presupuesto Global (EUR)  [Input Usuario]                        |
| Celda B2: Gastado en Firme (EUR)    [Output Agente]                        |
| Celda B3: Comprometido Lider (EUR)  [Output Agente]                        |
| Celda B4: Saldo Disponible (EUR)    [Output Agente]                        |
+----------------------------------------------------------------------------+
| Fila 6 (Cabeceras de Tabla):                                               |
| A: URL Catawiki          [Input Usuario]                                   |
| B: Puja Maxima (EUR)     [Input Usuario]                                   |
| C: Titulo del Reloj      [Output Agente]                                   |
| D: Hora de Cierre        [Output Agente]                                   |
| E: Estado                [Output Agente]                                   |
| F: Mi Ultima Puja (EUR)  [Output Agente]                                   |
| G: Puja Actual/Final     [Output Agente]                                   |
| H: Reserva Alcanzada     [Output Agente]                                   |
| I: Techo Dinamico (EUR)  [Output Agente]                                   |
| J: Notas / Ultimo Evento [Output Agente]                                   |
+----------------------------------------------------------------------------+
```

## Risks / Trade-offs

- **[Cambios en selectores DOM o traducciones de Catawiki]** → Mitigación: Abstraer los selectores e indicadores de texto (ES/EN) en un módulo `catawiki_selectors.py` configurable y priorizar la lectura de datos estructurados/atributos `data-testid` e interceptación de red JSON de la página del lote.
- **[Desfase de reloj local del PC frente al servidor de Catawiki]** → Mitigación: Utilizar la cuenta atrás relativa entregada por la propia página/servidor de Catawiki en tiempo real en lugar de confiar únicamente en el reloj del sistema operativo local.
- **[Usuario edita el Excel mientras hay cambios encolados en memoria]** → Mitigación: El agente separa estrictamente las columnas de entrada (`A`, `B` y celda `B1`) de las columnas de salida (`C`..`J` y celdas `B2:B4`); al volcar a disco, lee primero los valores actuales de las columnas de entrada del archivo en disco para no sobrescribir nunca una edición manual del usuario.
