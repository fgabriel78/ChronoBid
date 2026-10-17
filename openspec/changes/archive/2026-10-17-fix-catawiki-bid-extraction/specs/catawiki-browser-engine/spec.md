# Spec Delta: catawiki-browser-engine

## MODIFIED Requirements

### Requirement: Operación diferenciada en Modo Patrulla y Modo Asalto
El sistema SHALL monitorizar los relojes con cierre lejano (> 5 minutos) mediante consultas periódicas espaciadas (Modo Patrulla) y conmutar a seguimiento continuo en tiempo real (Modo Asalto) cuando falten menos de 5 minutos para el cierre de un reloj viable.

#### Scenario: Transición de Modo Patrulla a Modo Asalto
- **WHEN** un reloj viable entra en los últimos 5 minutos previos a su hora de cierre
- **THEN** el sistema abre o activa el seguimiento dedicado en tiempo real para ese lote con lectura sub-segundo del temporizador, puja actual, condición de líder y aviso de reserva

#### Scenario: Extracción precisa de puja actual y estado de subasta
- **WHEN** el sistema consulta un lote en Modo Patrulla o Modo Asalto
- **THEN** el sistema extrae el importe exacto de la puja actual, el siguiente escalón mínimo y el estado de reserva combinando el estado SSR inyectado por la plataforma con los selectores DOM actualizados del lote

### Requirement: Invisibilidad previa y ejecución de puja por incremento mínimo
El sistema SHALL abstenerse de realizar pujas hasta los últimos segundos configurados del cierre (< 25 segundos) y, al pujar, MUST enviar únicamente el siguiente escalón mínimo de puja exigido por Catawiki sin revelar el precio máximo del usuario a la plataforma.

#### Scenario: Primera entrada en los segundos finales
- **WHEN** faltan menos de 25 segundos para el cierre de un reloj, el usuario no es el máximo pujador actual y el siguiente escalón mínimo de puja no supera el techo dinámico asignado
- **THEN** el sistema ejecuta una puja por el importe exacto del siguiente escalón mínimo válido

#### Scenario: Envío de puja mediante botón rápido o campo numérico
- **WHEN** se ordena el envío del escalón mínimo de puja a la página del lote
- **THEN** el sistema pulsa el botón de puja rápida si coincide exactamente con el importe o introduce el valor en el campo de puja y confirma la acción en la interfaz
