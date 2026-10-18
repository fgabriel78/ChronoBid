# Proposal

## Why

Actualmente, el Modo Patrulla consulta secuencialmente todos los lotes activos cada 15 segundos constantes, sin importar si faltan días, horas o minutos para su cierre. Dado que cada consulta de patrulla abre una nueva pestaña vía CDP, navega la aplicación web SPA de Catawiki y renderiza el DOM, un intervalo rígido de 15 segundos somete al sistema y al navegador a un consumo excesivo e innecesario de CPU, memoria y ancho de banda. 

Dado que ChronoBid no realiza ninguna puja en Modo Patrulla y no interviene tácticamente hasta los últimos segundos antes del cierre, sustituir el intervalo fijo de 15 segundos por una cadencia escalonada y adaptativa por lote reducirá drásticamente el uso de recursos (> 85%) sin comprometer en absoluto la precisión de entrada a Modo Asalto ni las decisiones de puja.

## What Changes

- **Cadencia escalonada por lote en Modo Patrulla**: Sustituir el sondeo indiscriminado cada 15 segundos por una política adaptativa individual que calcula el intervalo de revisión según el tiempo restante de cada lote:
  - **Tramo Lejano** ($t > 3.600\text{ s}$ / $> 1\text{ hora}$): consulta cada 5 minutos ($300\text{ s}$).
  - **Tramo Medio** ($900\text{ s} < t \le 3.600\text{ s}$ / $15\text{ a }60\text{ min}$): consulta cada 2 minutos ($120\text{ s}$).
  - **Tramo Próximo** ($300\text{ s} < t \le 900\text{ s}$ / $5\text{ a }15\text{ min}$): consulta cada 60 segundos ($60\text{ s}$).
- **Seguimiento temporal por lote (`last_patrolled_at`)**: Registrar el instante de última consulta en cada lote o en el orquestador para omitir consultas innecesarias a lotes lejanos durante pasadas del bucle general.
- **Prioridad inmediata para lotes no explorados**: Lotes recién dados de alta o en estado `PENDING` sin snapshot previo se consultan de inmediato en la primera pasada disponible.
- **Anticipación adaptativa al umbral de Asalto**: Si un lote viable está en el tramo próximo y faltan menos segundos que su intervalo para llegar a los 300 s ($t - 300\text{ s} < \text{intervalo}$), programar la siguiente consulta para que coincida exactamente con la conmutación a Modo Asalto.
- **Configurabilidad en CLI**: Exponer parámetros o valores por defecto configurables si se requiere personalizar los intervalos desde línea de comandos.

## Capabilities

### New Capabilities
*(Ninguna nueva capability requerida; se enmarca dentro de las capacidades de orquestación y navegación existentes).*

### Modified Capabilities
- `catawiki-browser-engine`: Actualizar el requisito de *Operación diferenciada en Modo Patrulla y Modo Asalto* para especificar formalmente la cadencia escalonada y adaptativa por tramos de tiempo restante en Modo Patrulla.

## Impact

- `src/chronobid/orchestrator.py`: Lógica de determinación de intervalo escalonado por lote, filtrado de lotes listos para consulta en `run_patrol_step()`, y cálculo adaptativo del tiempo de espera del bucle principal.
- `src/chronobid/cli.py`: Exposición opcional de argumentos de configuración para los intervalos de patrulla.
- `tests/test_orchestrator_e2e.py`: Actualización y ampliación de tests unitarios y de integración para verificar los intervalos por tramos y la no interferencia con lotes concurrentes en asalto.
