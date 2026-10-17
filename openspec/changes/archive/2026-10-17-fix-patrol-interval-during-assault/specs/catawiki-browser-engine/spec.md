# Spec Delta

## MODIFIED Requirements

### Requirement: Operación diferenciada en Modo Patrulla y Modo Asalto
El sistema SHALL monitorizar los relojes con cierre lejano (> 5 minutos) mediante consultas periódicas espaciadas (Modo Patrulla) y conmutar a seguimiento continuo en tiempo real (Modo Asalto) cuando falten menos de 5 minutos para el cierre de un reloj viable, garantizando la independencia estricta de sus ritmos de consulta.

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

#### Scenario: Independencia de cadencia en Modo Patrulla durante Modo Asalto concurrente
- **WHEN** uno o más relojes viables se encuentran en seguimiento continuo de Modo Asalto y existen otros relojes viables con cierre superior a 5 minutos
- **THEN** el sistema mantiene la monitorización de los relojes en Modo Patrulla con consultas espaciadas según el intervalo de patrulla configurado, sin acelerar las consultas ni recargar continuamente las pestañas de patrulla debido a los asaltos concurrentes

#### Scenario: Suspensión de consultas de patrulla cuando todos los lotes activos están en asalto
- **WHEN** todos los relojes viables activos se encuentran ya en Modo Asalto con seguimiento dedicado
- **THEN** el sistema suspende las rondas de patrulla y aguarda pasivamente la resolución de las tareas de asalto sin realizar esperas activas de alta frecuencia ni consultas redundantes de mercado
