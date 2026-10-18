# catawiki-browser-engine Specification

## Purpose

Proporciona la interfaz de interacción con Catawiki sobre una sesión de navegador autenticada para extraer el estado en tiempo real de las subastas de relojes y ejecutar pujas tácticas en los segundos finales y prórrogas.

## Requirements

### Requirement: Conexión a sesión de navegador autenticada
El sistema SHALL conectarse a una instancia de navegador ya abierta y autenticada por el usuario en Catawiki, reutilizando sus credenciales, cookies de sesión y huella de navegador sin solicitar contraseñas ni requerir inicio de sesión automatizado.

#### Scenario: Conexión exitosa al navegador del usuario
- **WHEN** el agente se inicia y el navegador del usuario está disponible con una sesión activa en Catawiki
- **THEN** el sistema enlaza con el navegador y verifica que la sesión del usuario está autenticada antes de operar

#### Scenario: Navegador no disponible o sesión no iniciada
- **WHEN** el agente no puede enlazar con el navegador o detecta que el usuario no ha iniciado sesión en Catawiki
- **THEN** el sistema notifica claramente el requisito de conexión/autenticación y detiene el envío de pujas hasta que se restablezca

### Requirement: Operación diferenciada en Modo Patrulla y Modo Asalto
El sistema SHALL monitorizar los relojes con cierre lejano (> 5 minutos) mediante una cadencia escalonada y adaptativa según el tiempo restante de cada lote individual (Modo Patrulla) y conmutar a seguimiento continuo en tiempo real (Modo Asalto) cuando falten menos de 5 minutos para el cierre de un reloj viable, garantizando la independencia estricta de sus ritmos de consulta y la minimización del uso de recursos.

#### Scenario: Transición de Modo Patrulla a Modo Asalto
- **WHEN** un reloj viable entra en los últimos 5 minutos previos a su hora de cierre
- **THEN** el sistema abre o activa el seguimiento dedicado en tiempo real para ese lote con lectura sub-segundo del temporizador, puja actual, condición de líder y aviso de reserva

#### Scenario: Cadencia escalonada por tramos temporales en Modo Patrulla
- **WHEN** un lote activo en Modo Patrulla tiene asignado un tiempo restante conocido superior a 5 minutos
- **THEN** el sistema aplica una cadencia de revisión individualizada: cada 300 segundos si restan más de 3.600 segundos (> 1 hora), cada 120 segundos si restan entre 900 y 3.600 segundos (15 a 60 minutos), y cada 60 segundos si restan entre 300 y 900 segundos (5 a 15 minutos), evitando consultar el lote mientras no haya vencido su intervalo correspondiente

#### Scenario: Prioridad inmediata para lotes pendientes sin consulta previa
- **WHEN** se incorpora un nuevo lote al catálogo o un lote permanece en estado PENDING sin snapshot previo
- **THEN** el sistema lo prioriza e incluye inmediatamente en la siguiente ronda de patrulla sin esperar ningún intervalo temporal previo

#### Scenario: Anticipación adaptativa al umbral de Modo Asalto
- **WHEN** un reloj viable en el tramo próximo (5 a 15 minutos) se encuentra a un tiempo restante tal que la siguiente consulta periódica caería por debajo de los 300 segundos
- **THEN** el sistema ajusta dinámicamente la espera del bucle de patrulla para despertar y consultar el lote en el segundo exacto en que alcanza los 300 segundos restantes, conmutando a Modo Asalto sin demoras

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
- **THEN** el sistema mantiene la monitorización de los relojes en Modo Patrulla con consultas espaciadas según el intervalo de patrulla escalonado correspondiente a cada lote, sin acelerar las consultas ni recargar continuamente las pestañas de patrulla debido a los asaltos concurrentes

#### Scenario: Suspensión de consultas de patrulla cuando todos los lotes activos están en asalto
- **WHEN** todos los relojes viables activos se encuentran ya en Modo Asalto con seguimiento dedicado
- **THEN** el sistema suspende las rondas de patrulla y aguarda pasivamente la resolución de las tareas de asalto sin realizar esperas activas de alta frecuencia ni consultas redundantes de mercado

### Requirement: Resiliencia ante errores transitorios de carga en Catawiki
El sistema SHALL detectar páginas de error transitorio de Catawiki, respuestas de fallo de plataforma o ausencia de contenedores de subasta, catalogándolas como incidencias temporales sin sobreescribir el título del lote ni forzar el temporizador a cero.

#### Scenario: Detección de pantalla de error tipo We can't load this page right now
- **WHEN** el sistema navega a la URL de un lote y la página muestra encabezados o textos de error como "We can't load this page right now", fallos HTTP 5xx o pantallas sin contenedores de subasta
- **THEN** el sistema rechaza la extracción como snapshot de mercado inválido, preserva el título y estado previo del lote y evita transicionar la subasta a cerrada o perdida

#### Scenario: Preservación de cuenta atrás ausente en lote activo
- **WHEN** una página de subasta abierta se carga pero los selectores de cuenta atrás no devuelven un valor numérico y no existe texto explícito de lote cerrado
- **THEN** el sistema registra el tiempo restante como nulo (`None`) en lugar de forzarlo a `0.0`, impidiendo que la subasta sea interpretada como concluida por expiración de tiempo

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

### Requirement: Gestión del bucle de prórrogas de +90 segundos
El sistema SHALL continuar monitorizando el lote tras cualquier extensión de tiempo (+90 segundos) provocada por pujas en el último minuto y contraatacar cuando otro postor supere la puja del usuario, siempre que el nuevo escalón mínimo no exceda el techo dinámico ni el presupuesto disponible.

#### Scenario: Contraataque durante la prórroga de 90 segundos
- **WHEN** un competidor supera la puja del usuario durante la ventana de prórroga y el siguiente incremento mínimo está dentro del techo dinámico vigente
- **THEN** el sistema envía la contrapuja mínima requerida y actualiza la nueva cuenta atrás de cierre

#### Scenario: Abandono cuando el rival supera el techo permitido
- **WHEN** la puja necesaria para superar al líder actual excede el techo dinámico o la puja máxima del reloj
- **THEN** el sistema detiene las pujas sobre ese lote y libera de inmediato cualquier presupuesto previamente comprometido en él

### Requirement: Desbloqueo escalonado del precio de reserva en los segundos finales
El sistema SHALL incrementar su propia puja escalón a escalón en los últimos segundos cuando el usuario ya sea el máximo pujador pero la subasta indique que no se ha alcanzado el precio de reserva, deteniéndose inmediatamente en cuanto el aviso de reserva desaparezca o se alcance el techo permitido.

#### Scenario: Reserva desbloqueada antes de agotar el techo
- **WHEN** el usuario es líder en los últimos 25 segundos con aviso de "precio de reserva no alcanzado" y el sistema sube un escalón mínimo tras el cual desaparece dicho aviso
- **THEN** el sistema detiene inmediatamente las autopujas adicionales y mantiene la posición ganadora al coste mínimo de desbloqueo

#### Scenario: Reserva por encima del máximo permitido
- **WHEN** el sistema sube escalones intentando alcanzar la reserva y el siguiente incremento supera la puja máxima del reloj o el techo dinámico disponible
- **THEN** el sistema deja de autopujar sin superar jamás el límite establecido
