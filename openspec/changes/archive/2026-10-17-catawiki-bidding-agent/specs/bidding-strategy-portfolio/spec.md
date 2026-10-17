# Spec Delta

## Purpose

Gobierna la contabilidad atómica del presupuesto global en euros y la estrategia de optimización dinámica de cartera para maximizar el número total de relojes ganados sin exceder nunca los límites individuales ni el presupuesto global.

## ADDED Requirements

### Requirement: Contabilidad atómica de presupuesto en vuelo (Ledger)
El sistema SHALL mantener una contabilidad atómica en tiempo real donde el presupuesto disponible sea igual al presupuesto global menos la suma de relojes ya ganados y menos la suma de las pujas activas en las que el usuario es actualmente el máximo pujador provisional.

#### Scenario: Bloqueo de saldo al convertirse en máximo pujador provisional
- **WHEN** el agente realiza una puja y pasa a ser el líder provisional de un reloj por un importe $X$
- **THEN** el sistema descuenta $X$ (o el incremento diferencial si ya era líder por un importe menor) del presupuesto disponible de forma atómica antes de permitir pujas en otros relojes concurrentes

#### Scenario: Liberación inmediata de saldo al ser superado por otro postor
- **WHEN** otro usuario supera la puja del agente en una subasta activa
- **THEN** el sistema libera al instante el importe previamente comprometido en ese reloj y lo reincorpora al presupuesto disponible

### Requirement: Invariante estricto de techo individual y presupuesto global
El sistema MUST rechazar cualquier intento de puja cuyo importe supere la puja máxima limpia definida en el Excel para ese reloj o cuyo desembolso adicional supere el presupuesto global disponible en ese instante.

#### Scenario: Intento de puja que excedería el presupuesto disponible por subastas solapadas
- **WHEN** dos relojes se encuentran en cierre simultáneo y la puja requerida en el segundo reloj supera el presupuesto disponible tras descontar la puja líder activa en el primer reloj
- **THEN** el sistema bloquea la puja en el segundo reloj para garantizar que la suma total de compromisos nunca supere el presupuesto global

### Requirement: Poda automática de relojes fuera de precio
El sistema SHALL descartar automáticamente de la cartera activa cualquier reloj cuya siguiente puja mínima requerida en Catawiki supere la puja máxima limpia indicada por el usuario en el Excel.

#### Scenario: Reloj supera el máximo antes de entrar en el minuto final
- **WHEN** durante la monitorización se detecta que el siguiente escalón mínimo de puja de un reloj es mayor que su puja máxima en el Excel
- **THEN** el sistema marca el reloj como descartado por superar el máximo y lo excluye del cálculo de reserva de presupuesto para futuros lotes

### Requirement: Cálculo de techo dinámico para maximizar el número de relojes
El sistema SHALL calcular dinámicamente un techo efectivo ($E_i \le M_i$) para cada subasta activa evaluando el coste actual y la holgura de precio de los demás relojes viables pendientes, reservando presupuesto cuando una combinación de relojes futuros de alta viabilidad permita adquirir un mayor número de unidades con el mismo saldo disponible.

#### Scenario: Reserva de presupuesto ante dos relojes futuros baratos y con alta holgura
- **WHEN** un reloj activo requiere consumir un importe que dejaría sin saldo suficiente a una combinación de dos o más relojes futuros viables que cuentan con amplio margen bajo sus respectivos máximos
- **THEN** el sistema reduce el techo dinámico efectivo del reloj activo para preservar el saldo necesario para la combinación que maximiza el número de relojes

#### Scenario: Liberación del techo completo cuando los relojes futuros pierden viabilidad
- **WHEN** los relojes futuros pendientes suben de precio y reducen su margen de holgura o quedan descartados
- **THEN** el sistema eleva automáticamente el techo dinámico del reloj activo hasta el máximo permitido por su tope individual ($M_i$) y el presupuesto disponible
