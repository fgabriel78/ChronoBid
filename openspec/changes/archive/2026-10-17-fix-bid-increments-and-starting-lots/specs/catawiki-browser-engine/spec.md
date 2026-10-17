# Spec Delta: catawiki-browser-engine

## MODIFIED Requirements

### Requirement: Operación diferenciada en Modo Patrulla y Modo Asalto
El sistema SHALL monitorizar los relojes con cierre lejano (> 5 minutos) mediante consultas periódicas espaciadas (Modo Patrulla) y conmutar a seguimiento continuo en tiempo real (Modo Asalto) cuando falten menos de 5 minutos para el cierre de un reloj viable.

#### Scenario: Transición de Modo Patrulla a Modo Asalto
- **WHEN** un reloj viable entra en los últimos 5 minutos previos a su hora de cierre
- **THEN** el sistema abre o activa el seguimiento dedicado en tiempo real para ese lote con lectura sub-segundo del temporizador, puja actual, condición de líder y aviso de reserva

#### Scenario: Extracción precisa de puja actual y estado de subasta
- **WHEN** el sistema consulta un lote en Modo Patrulla o Modo Asalto
- **THEN** el sistema extrae el importe exacto de la puja actual, calcula el siguiente escalón mínimo conforme a los tramos oficiales de incremento de Catawiki (incluyendo micro-pujas de 1-10 € a +1 €) y clasifica el estado de reserva (MET, NOT_MET o NO_RESERVE) combinando el estado SSR inyectado con los selectores DOM del lote

#### Scenario: Tratamiento de lotes en apertura inicial sin pujas previas
- **WHEN** un lote activo en consulta no registra ninguna puja previa en su historial o muestra estado inicial de apertura
- **THEN** el sistema adopta el importe de salida o primer botón de puja rápida como siguiente escalón mínimo admisible sin descartar el lote por comparativa estricta contra una puja actual ficticia
