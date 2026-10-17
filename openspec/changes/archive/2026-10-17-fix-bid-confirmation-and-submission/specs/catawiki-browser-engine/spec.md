# Spec Delta

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
- **THEN** el sistema adopta el importe de salida o primer botón de puja rápida como siguiente escalón mínimo admisible sin descartar el lote por comparativa estricta contra una puja actual ficticia, y en cuanto el DOM en vivo o la red reflejen que ya se ha registrado una puja o el liderazgo del usuario en la misma pestaña, deja de forzar la puja actual a cero aunque el estado SSR inicial permanezca sin recargar

#### Scenario: Tolerancia a fallo puntual durante la ronda de patrulla
- **WHEN** la consulta a un lote durante la patrulla devuelve una lectura de error temporal de red o plataforma
- **THEN** el orquestador mantiene el lote en su estado activo, no propaga valores vacíos o cerrados al modelo y reintenta la lectura en la siguiente ronda de patrulla

### Requirement: Invisibilidad previa y ejecución de puja por incremento mínimo
El sistema SHALL abstenerse de realizar pujas hasta los últimos segundos configurados del cierre (< 25 segundos) y, al pujar, MUST enviar únicamente el siguiente escalón mínimo de puja exigido por Catawiki sin revelar el precio máximo del usuario a la plataforma.

#### Scenario: Primera entrada en los segundos finales
- **WHEN** faltan menos de 25 segundos para el cierre de un reloj, el usuario no es el máximo pujador actual y el siguiente escalón mínimo de puja no supera el techo dinámico asignado
- **THEN** el sistema ejecuta una puja por el importe exacto del siguiente escalón mínimo válido

#### Scenario: Envío de puja mediante botón rápido o campo numérico
- **WHEN** se ordena el envío del escalón mínimo de puja a la página del lote
- **THEN** el sistema pulsa el botón de puja rápida si coincide exactamente con el importe o introduce el valor en el campo de puja y pulsa el botón de puja directa (nunca el de fijar puja máxima automática), espera activamente a que se monte el diálogo modal de confirmación de Catawiki, pulsa el botón principal de confirmar puja y aguarda a la actualización del estado antes de extraer la nueva instantánea

#### Scenario: Registro condicionado a la aceptación efectiva de la puja
- **WHEN** finaliza el intento de envío y confirmación de una puja en Modo Asalto y se obtiene la instantánea posterior del lote
- **THEN** el orquestador registra la puja propia únicamente si la instantánea confirma que el usuario es líder o que la puja actual del mercado ha alcanzado al menos el importe enviado; en caso contrario, libera de inmediato el presupuesto reservado y no marca falsamente el lote como superado
