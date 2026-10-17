# Spec Delta

## ADDED Requirements

### Requirement: Resiliencia ante errores transitorios de carga en Catawiki
El sistema SHALL detectar páginas de error transitorio de Catawiki, respuestas de fallo de plataforma o ausencia de contenedores de subasta, catalogándolas como incidencias temporales sin sobreescribir el título del lote ni forzar el temporizador a cero.

#### Scenario: Detección de pantalla de error tipo We can't load this page right now
- **WHEN** el sistema navega a la URL de un lote y la página muestra encabezados o textos de error como "We can't load this page right now", fallos HTTP 5xx o pantallas sin contenedores de subasta
- **THEN** el sistema rechaza la extracción como snapshot de mercado inválido, preserva el título y estado previo del lote y evita transicionar la subasta a cerrada o perdida

#### Scenario: Preservación de cuenta atrás ausente en lote activo
- **WHEN** una página de subasta abierta se carga pero los selectores de cuenta atrás no devuelven un valor numérico y no existe texto explícito de lote cerrado
- **THEN** el sistema registra el tiempo restante como nulo (`None`) en lugar de forzarlo a `0.0`, impidiendo que la subasta sea interpretada como concluida por expiración de tiempo

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

#### Scenario: Tolerancia a fallo puntual durante la ronda de patrulla
- **WHEN** la consulta a un lote durante la patrulla devuelve una lectura de error temporal de red o plataforma
- **THEN** el orquestador mantiene el lote en su estado activo, no propaga valores vacíos o cerrados al modelo y reintenta la lectura en la siguiente ronda de patrulla
