# Proposal

## Why

Cuando uno o más relojes entran en Modo Asalto (<= 5 minutos), el bucle principal del orquestador colapsa erróneamente el intervalo de espera entre consultas de patrulla de 15 segundos a solo 0.05 segundos (50 ms). Esto provoca que la pestaña de Chrome encargada de patrullar los relojes lejanos (> 5 minutos) se abra, navegue y refresque incesantemente a alta frecuencia, consumiendo CPU, red y saturando el navegador de forma innecesaria e ineficiente.

El sistema debe desacoplar estrictamente el ritmo de seguimiento: los relojes en Modo Asalto deben monitorizarse en tiempo real (0.25 s) en sus pestañas dedicadas, mientras que los relojes en Modo Patrulla deben mantener su cadencia espaciada independiente (15 s por defecto), incluso cuando haya asaltos concurrentes en curso.

## What Changes

- **Desacoplo de intervalos en el bucle del orquestador**: Modificar la condición de espera en `AuctionOrchestrator.run_until_complete()` para que mientras existan lotes activos que requieran patrulla, el bucle duerma rigurosamente el intervalo de patrulla (`patrol_interval_seconds`).
- **Espera pasiva sin busy-wait cuando todos los lotes están en asalto**: Si todos los lotes viables activos ya están gestionados en tareas concurrentes de asalto, suspender las consultas de patrulla y esperar pasivamente la finalización de alguna tarea de asalto o una señal de parada, eliminando el ciclo vacío de 50 ms.
- **Anticipación adaptativa al umbral de asalto**: Ajustar dinámicamente el tiempo de espera del ciclo de patrulla si algún lote viable está a menos de `patrol_interval_seconds` de cruzar el umbral de los 5 minutos, conmutando a asalto en el momento preciso.

## Capabilities

### New Capabilities
<!-- No se introducen nuevas capacidades de sistema. -->

### Modified Capabilities
- `catawiki-browser-engine`: Actualizar el requisito `Operación diferenciada en Modo Patrulla y Modo Asalto` para especificar formalmente que la existencia de tareas concurrentes en Modo Asalto no acelera ni altera la cadencia espaciada de las consultas a los relojes en Modo Patrulla, y que la ausencia de lotes en patrulla suspende el bucle de patrulla sin consumir ciclos en espera activa.

## Impact

- **Código afectado**: `src/chronobid/orchestrator.py` (`run_until_complete` y cálculo del intervalo de espera).
- **Pruebas**: Nuevos tests en `tests/test_orchestrator_e2e.py` simulando escenarios mixtos con relojes concurrentes en Modo Asalto y Modo Patrulla, verificando que los relojes en patrulla no se consultan en ráfaga rápida durante el asalto.
- **Rendimiento y estabilidad**: Reducción drástica del uso de CPU y peticiones innecesarias a Catawiki en Chrome durante los minutos finales de cualquier subasta.
