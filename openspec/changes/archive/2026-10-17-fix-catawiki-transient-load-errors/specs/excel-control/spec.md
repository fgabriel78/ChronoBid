# Spec Delta

## MODIFIED Requirements

### Requirement: Reporte de estado de subasta y métricas por reloj
El sistema SHALL escribir y mantener actualizadas en el fichero Excel las columnas de control de cada reloj: título del lote, hora de cierre, estado de la subasta, última puja propia, puja actual o ganadora, estado del precio de reserva y techo dinámico asignado.

#### Scenario: Actualización de estado tras realizar una puja
- **WHEN** el agente ejecuta una puja válida en Catawiki sobre un reloj de la lista
- **THEN** el sistema actualiza la fila correspondiente en el Excel reflejando el importe en "Mi Última Puja", la puja actual del lote y el estado "LIDER" o el que corresponda

#### Scenario: Registro de resultado final de la subasta
- **WHEN** una subasta finaliza definitivamente tras agotar su tiempo y prórrogas
- **THEN** el sistema escribe en el Excel el estado final ("GANADO", "PERDIDO: Superó Máximo", "PERDIDO: Sin Presupuesto" o "NO ADJUDICADO: Reserva No Alcanzada") junto con el precio de cierre

#### Scenario: Preservación de título y estado activo ante fallo transitorio de consulta
- **WHEN** la consulta a Catawiki sobre un lote activo falla temporalmente por degradación de servicio o error de red
- **THEN** el sistema preserva en el Excel el título legítimo previamente registrado y el estado activo del reloj, anotando la incidencia de reintento en "Notas / Último Evento" sin marcar la subasta como "PERDIDO" ni cerrar el lote
