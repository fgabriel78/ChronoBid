# Spec Delta

## MODIFIED Requirements

### Requirement: Contabilidad atómica de presupuesto en vuelo (Ledger)
El sistema SHALL mantener una contabilidad atómica en tiempo real donde el presupuesto disponible sea igual al presupuesto global menos la suma de relojes ya ganados y menos la suma de las pujas activas en las que el usuario es actualmente el máximo pujador provisional.

#### Scenario: Bloqueo de saldo al convertirse en máximo pujador provisional
- **WHEN** el agente realiza una puja y pasa a ser el líder provisional de un reloj por un importe $X$
- **THEN** el sistema descuenta $X$ (o el incremento diferencial si ya era líder por un importe menor) del presupuesto disponible de forma atómica antes de permitir pujas en otros relojes concurrentes

#### Scenario: Sincronización de saldo comprometido al detectar liderazgo en lectura de mercado
- **WHEN** el sistema consulta un lote activo en Modo Patrulla o Modo Asalto y detecta que el usuario es el máximo pujador provisional por un importe $X > 0$ (incluyendo pujas manuales previas en Catawiki o sesiones reiniciadas)
- **THEN** el sistema actualiza de forma atómica el importe comprometido como líder para ese reloj a $X$ y descuenta dicho compromiso del presupuesto disponible

#### Scenario: Liberación inmediata de saldo al ser superado por otro postor
- **WHEN** otro usuario supera la puja del usuario en una subasta activa, tanto durante el Modo Patrulla como durante el Modo Asalto
- **THEN** el sistema libera al instante el importe previamente comprometido en ese reloj y lo reincorpora al presupuesto disponible
