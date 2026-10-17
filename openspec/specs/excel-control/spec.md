# excel-control Specification

## Purpose

Gestiona la lectura de parámetros de entrada (presupuesto global en euros, URLs de relojes y pujas máximas limpias) y la actualización asíncrona del estado de las subastas en un fichero Excel en segundo plano sin bloquear la operativa de pujas.

## Requirements

### Requirement: Lectura de configuración global y catálogo de relojes
El sistema SHALL leer de la hoja de cálculo Excel el presupuesto máximo global en euros y la lista de relojes candidatos compuestos por su URL de Catawiki y la puja máxima limpia autorizada por el usuario, detectando modificaciones en caliente y reconciliando el estado activo en memoria durante la ejecución.

#### Scenario: Carga inicial válida del fichero Excel
- **WHEN** el agente inicia o recarga el fichero Excel con un presupuesto global positivo y filas con URL y puja máxima limpia
- **THEN** el sistema registra el presupuesto global y crea una entrada de seguimiento activa para cada reloj válido

#### Scenario: Fila con datos incompletos o inválidos
- **WHEN** una fila del fichero Excel carece de URL de Catawiki o tiene una puja máxima no numérica o menor o igual a cero
- **THEN** el sistema marca el estado de esa fila como error de validación y continúa procesando el resto de relojes

#### Scenario: Reconciliación dinámica por modificación de presupuesto global en caliente
- **WHEN** el usuario modifica la celda de Presupuesto Global (B1) en el fichero Excel y guarda los cambios durante la ejecución
- **THEN** el sistema detecta la modificación, actualiza el presupuesto global en el registro atómico (Ledger) y recalcula el saldo disponible y los techos dinámicos de cartera sin reiniciar el orquestador

#### Scenario: Incorporación dinámica de nuevos relojes en caliente
- **WHEN** el usuario añade una nueva fila con URL de Catawiki y puja máxima válida en el fichero Excel durante la ejecución
- **THEN** el sistema incorpora el nuevo lote a la cartera activa en estado pendiente y activa una consulta inmediata de patrulla para extraer sus datos de mercado

#### Scenario: Modificación dinámica de puja máxima y reactivación de lotes viables
- **WHEN** el usuario modifica la puja máxima de un reloj existente en el fichero Excel
- **THEN** el sistema actualiza el tope individual del reloj, y si el reloj se encontraba descartado por haber superado su tope previo y la nueva puja máxima es mayor o igual a la puja mínima requerida en Catawiki, reactiva el reloj a estado activo (Modo Patrulla o Modo Asalto según su tiempo de cierre)

#### Scenario: Eliminación de relojes del Excel durante la ejecución
- **WHEN** el usuario elimina una fila o vacía la URL de un reloj en el fichero Excel
- **THEN** el sistema cancela el seguimiento del reloj, detiene de forma ordenada cualquier tarea de asalto dedicada a ese lote, libera su presupuesto comprometido en el Ledger si no se encuentra adjudicado y lo retira de la cartera activa

#### Scenario: Tolerancia a bloqueo de lectura por edición simultánea en Excel
- **WHEN** el orquestador intenta leer el fichero Excel para verificar cambios mientras el usuario lo mantiene abierto y bloqueado para guardado
- **THEN** el sistema tolera el error puntual de compartición de archivos en Windows (PermissionError/OSError) manteniendo intacto el estado en memoria y reintentando la lectura en la siguiente verificación

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

### Requirement: Reporte de contabilidad del presupuesto global en Excel
El sistema SHALL actualizar en la cabecera del fichero Excel el desglose del presupuesto global: importe gastado en firme (relojes ganados), importe comprometido en vuelo (relojes activos donde el usuario es líder) y saldo disponible.

#### Scenario: Reflejo de presupuesto comprometido y disponible
- **WHEN** el usuario pasa a ser el máximo pujador provisional en una subasta activa o gana un lote
- **THEN** el sistema recalcula y actualiza en el Excel las celdas de presupuesto gastado, comprometido y disponible

### Requirement: Escritura en segundo plano tolerante a bloqueo de archivo
El sistema SHALL encolar en memoria las actualizaciones pendientes hacia el fichero Excel cuando el archivo esté bloqueado por otro proceso en el sistema operativo, sin retrasar ni interrumpir la monitorización ni las pujas en curso.

#### Scenario: Usuario abre el Excel durante una subasta activa
- **WHEN** el agente intenta guardar un cambio de estado en el fichero Excel mientras el usuario lo tiene abierto y bloqueado para escritura
- **THEN** el sistema conserva el estado actualizado en memoria, continúa ejecutando pujas con normalidad y persiste los cambios pendientes automáticamente en cuanto el archivo vuelve a estar libre
