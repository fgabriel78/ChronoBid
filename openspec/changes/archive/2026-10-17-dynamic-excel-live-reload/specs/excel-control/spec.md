# Spec Delta

## MODIFIED Requirements

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
