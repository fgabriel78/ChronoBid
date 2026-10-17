# Spec Delta

## Purpose

Proporciona la interfaz de interacción con Catawiki sobre una sesión de navegador autenticada para extraer el estado en tiempo real de las subastas de relojes y ejecutar pujas tácticas en los segundos finales y prórrogas.

## ADDED Requirements

### Requirement: Conexión a sesión de navegador autenticada
El sistema SHALL conectarse a una instancia de navegador ya abierta y autenticada por el usuario en Catawiki, reutilizando sus credenciales, cookies de sesión y huella de navegador sin solicitar contraseñas ni requerir inicio de sesión automatizado.

#### Scenario: Conexión exitosa al navegador del usuario
- **WHEN** el agente se inicia y el navegador del usuario está disponible con una sesión activa en Catawiki
- **THEN** el sistema enlaza con el navegador y verifica que la sesión del usuario está autenticada antes de operar

#### Scenario: Navegador no disponible o sesión no iniciada
- **WHEN** el agente no puede enlazar con el navegador o detecta que el usuario no ha iniciado sesión en Catawiki
- **THEN** el sistema notifica claramente el requisito de conexión/autenticación y detiene el envío de pujas hasta que se restablezca

### Requirement: Operación diferenciada en Modo Patrulla y Modo Asalto
El sistema SHALL monitorizar los relojes con cierre lejano (> 5 minutos) mediante consultas periódicas espaciadas (Modo Patrulla) y conmutar a seguimiento continuo en tiempo real (Modo Asalto) cuando falten menos de 5 minutos para el cierre de un reloj viable.

#### Scenario: Transición de Modo Patrulla a Modo Asalto
- **WHEN** un reloj viable entra en los últimos 5 minutos previos a su hora de cierre
- **THEN** el sistema abre o activa el seguimiento dedicado en tiempo real para ese lote con lectura sub-segundo del temporizador, puja actual, condición de líder y aviso de reserva

### Requirement: Invisibilidad previa y ejecución de puja por incremento mínimo
El sistema SHALL abstenerse de realizar pujas hasta los últimos segundos configurados del cierre (< 25 segundos) y, al pujar, MUST enviar únicamente el siguiente escalón mínimo de puja exigido por Catawiki sin revelar el precio máximo del usuario a la plataforma.

#### Scenario: Primera entrada en los segundos finales
- **WHEN** faltan menos de 25 segundos para el cierre de un reloj, el usuario no es el máximo pujador actual y el siguiente escalón mínimo de puja no supera el techo dinámico asignado
- **THEN** el sistema ejecuta una puja por el importe exacto del siguiente escalón mínimo válido

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
