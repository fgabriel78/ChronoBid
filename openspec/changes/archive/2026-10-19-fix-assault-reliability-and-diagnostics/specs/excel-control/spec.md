# Spec Delta

## MODIFIED Requirements

### Requirement: Reporting auction status and metrics per watch
The system SHALL write and keep updated in the Excel file the control columns for each watch: lot title, closing time, auction status, own last bid, current or winning bid, reserve price status, and assigned dynamic ceiling.

#### Scenario: Status update after placing a bid
- **WHEN** the agent executes a valid bid on Catawiki for a watch in the list
- **THEN** the system updates the corresponding row in Excel reflecting the amount in "Mi Última Puja", the lot's current bid, and "LIDER" status or applicable status

#### Scenario: Recording final auction outcome
- **WHEN** an auction concludes definitively after exhausting its time and extensions
- **THEN** the system writes the final status to Excel ("GANADO", "PERDIDO: Superó Máximo", "PERDIDO: Sin Presupuesto", "PERDIDO", or "NO ADJUDICADO: Reserva No Alcanzada") along with the closing price

#### Scenario: Attribution of loss due to budget
- **WHEN** a lot closes without being won, its closing price is within the watch's maximum bid, and during the lot's bidding window a bid that was otherwise due was blocked because the next minimum step exceeded the dynamic ceiling or the available budget
- **THEN** the system records the final status "PERDIDO: Sin Presupuesto"

#### Scenario: Loss without budget block
- **WHEN** a lot closes without being won, its closing price is within the watch's maximum bid, and no due bid was blocked by the dynamic ceiling or the available budget (for example, the bid failed, was not confirmed, was blocked by human verification, or was never due)
- **THEN** the system records the final status "PERDIDO" and never "PERDIDO: Sin Presupuesto"

#### Scenario: Terminal status is final
- **WHEN** a lot has already been assigned a terminal status and is reconciled again (for example, during final consolidation of its assault task or a later dashboard refresh)
- **THEN** the system keeps the previously assigned terminal status unchanged

#### Scenario: Preservation of the last meaningful event on closure
- **WHEN** a lot reaches a terminal status after a meaningful pre-closure event was noted (bid failure, unconfirmed bid, verification block, ceiling or budget block)
- **THEN** the "Notas / Último Evento" column contains the closing summary followed by that last meaningful event instead of only a generic closing message

#### Scenario: Reporting verification-blocked status
- **WHEN** an active lot is blocked by an unresolved human verification challenge
- **THEN** the system writes "BLOQUEADO: Verificación Humana" in the status column and the detection details in "Notas / Último Evento"

#### Scenario: Preservation of title and active status on transient query failure
- **WHEN** querying Catawiki for an active lot fails temporarily due to service degradation or network error
- **THEN** the system preserves in Excel the legitimate title previously recorded and the active status of the watch, noting the retry event in "Notas / Último Evento" without marking the auction as "PERDIDO" or closing the lot
