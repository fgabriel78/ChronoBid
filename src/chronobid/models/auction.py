"""Domain models for watch lots, auction states, and budget."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from urllib.parse import urlparse

ERROR_TITLE_PHRASES: tuple[str, ...] = (
    "we can't load this page right now",
    "we cannot load this page right now",
    "no podemos cargar esta página en este momento",
    "no podemos cargar esta página",
    "something went wrong",
    "algo ha salido mal",
    "página no encontrada",
    "page not found",
    "access denied",
    "just a moment...",
)


class AuctionStatus(str, Enum):
    """Possible statuses for a watch lot on Catawiki."""

    PENDING = "PENDIENTE"
    PATROL = "PATRULLA"
    ASSAULT = "ASALTO"
    LEADER = "LIDER"
    OUTBID = "SUPERADO"
    WON = "GANADO"
    DISCARDED_MAX_EXCEEDED = "PERDIDO: Superó Máximo"
    LOST_NO_BUDGET = "PERDIDO: Sin Presupuesto"
    NOT_AWARDED_RESERVE = "NO ADJUDICADO: Reserva No Alcanzada"
    LOST_OTHER = "PERDIDO"
    VALIDATION_ERROR = "ERROR: Datos Inválidos"

    # Semantic alias for portfolio pruning references
    DESCARTADO = "PERDIDO: Superó Máximo"

    @property
    def is_terminal(self) -> bool:
        """Indicates whether the auction or tracking for this lot has finished."""
        return self in {
            AuctionStatus.WON,
            AuctionStatus.DISCARDED_MAX_EXCEEDED,
            AuctionStatus.LOST_NO_BUDGET,
            AuctionStatus.NOT_AWARDED_RESERVE,
            AuctionStatus.LOST_OTHER,
            AuctionStatus.VALIDATION_ERROR,
        }

    @property
    def is_active(self) -> bool:
        """Indicates whether the lot is still active and eligible for tracking."""
        return not self.is_terminal


class ReserveStatus(str, Enum):
    """Reserve price status of a lot on Catawiki."""

    UNKNOWN = "DESCONOCIDA"
    NO_RESERVE = "SIN RESERVA"
    NOT_MET = "NO ALCANZADA"
    MET = "ALCANZADA"

    @property
    def is_satisfied(self) -> bool:
        """Returns True if there is no pending unmet reserve price."""
        return self in {ReserveStatus.NO_RESERVE, ReserveStatus.MET}


@dataclass(frozen=True)
class GlobalBudgetState:
    """Immutable snapshot of global budget accounting in euros."""

    global_budget_eur: int
    spent_eur: int = 0
    committed_leader_eur: int = 0

    def __post_init__(self) -> None:
        if self.global_budget_eur < 0:
            raise ValueError("El presupuesto global no puede ser negativo.")
        if self.spent_eur < 0:
            raise ValueError("El importe gastado no puede ser negativo.")
        if self.committed_leader_eur < 0:
            raise ValueError("El importe comprometido no puede ser negativo.")
        if self.spent_eur + self.committed_leader_eur > self.global_budget_eur:
            raise ValueError(
                f"Invariante de presupuesto violado: gastado ({self.spent_eur}) + "
                f"comprometido ({self.committed_leader_eur}) > global ({self.global_budget_eur})."
            )

    @property
    def available_eur(self) -> int:
        """Current available balance: Global - Spent - Committed as leader."""
        return self.global_budget_eur - self.spent_eur - self.committed_leader_eur

    def can_afford_delta(self, delta_eur: int) -> bool:
        """Checks whether an additional disbursement fits within available budget."""
        return 0 <= delta_eur <= self.available_eur


@dataclass(frozen=True)
class BidDecision:
    """Result of a tactical bid evaluation for an active lot."""

    should_bid: bool
    bid_amount_eur: int | None = None
    reason: str = ""
    is_reserve_step: bool = False
    dynamic_ceiling_eur: int = 0

    def __post_init__(self) -> None:
        if self.should_bid:
            if self.bid_amount_eur is None or self.bid_amount_eur <= 0:
                raise ValueError(
                    "Una decisión de puja afirmativa requiere un importe entero positivo."
                )
            if self.dynamic_ceiling_eur > 0 and self.bid_amount_eur > self.dynamic_ceiling_eur:
                raise ValueError(
                    f"La puja ({self.bid_amount_eur} €) no puede superar el techo dinámico "
                    f"({self.dynamic_ceiling_eur} €)."
                )


def is_valid_catawiki_url(url: str) -> bool:
    """Validates that a string is a valid HTTP(S) lot URL."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    return True


def extract_lot_id(url: str, fallback_row: int | None = None) -> str:
    """Extracts a readable lot identifier from its Catawiki URL."""
    if url:
        cleaned = url.strip().rstrip("/")
        parts = [p for p in cleaned.split("/") if p]
        for part in reversed(parts):
            digits = "".join(ch for ch in part.split("-")[0] if ch.isdigit())
            if len(digits) >= 4:
                return f"lot-{digits}"
        if parts:
            return parts[-1][:40]
    if fallback_row is not None:
        return f"row-{fallback_row}"
    return "lot-unknown"


@dataclass
class WatchLot:
    """Domain entity representing a candidate watch and its real-time state."""

    lot_id: str
    url: str
    max_bid_eur: int
    row_index: int | None = None
    title: str = ""
    closing_time: datetime | None = None
    seconds_remaining: float | None = None
    status: AuctionStatus = AuctionStatus.PENDING
    my_last_bid_eur: int = 0
    current_bid_eur: int = 0
    next_min_bid_eur: int = 0
    is_leader: bool = False
    reserve_status: ReserveStatus = ReserveStatus.UNKNOWN
    dynamic_ceiling_eur: int = 0
    notes: str = ""
    is_closed: bool = False

    def __post_init__(self) -> None:
        if not self.lot_id:
            self.lot_id = extract_lot_id(self.url, self.row_index)
        if not self.validate_inputs():
            self.status = AuctionStatus.VALIDATION_ERROR
            if not self.notes:
                self.notes = "URL o Puja Máxima inválida"
        elif self.dynamic_ceiling_eur == 0 and self.max_bid_eur > 0:
            self.dynamic_ceiling_eur = self.max_bid_eur

    def validate_inputs(self) -> bool:
        """Verifies that the URL is valid and the clean maximum bid is > 0."""
        if not is_valid_catawiki_url(self.url):
            return False
        if not isinstance(self.max_bid_eur, int) or isinstance(self.max_bid_eur, bool):
            return False
        return self.max_bid_eur > 0

    @property
    def is_viable(self) -> bool:
        """Indicates whether the lot is still alive and within individual maximum budget."""
        if self.status.is_terminal or self.is_closed:
            return False
        if not self.is_leader and self.next_min_bid_eur > self.max_bid_eur:
            return False
        return True

    @property
    def effective_ceiling_eur(self) -> int:
        """Returns the effective ceiling, never exceeding the lot's clean maximum bid."""
        if self.dynamic_ceiling_eur <= 0:
            return max(0, self.max_bid_eur)
        return min(self.max_bid_eur, self.dynamic_ceiling_eur)

    def can_bid_amount(self, amount_eur: int) -> bool:
        """Verifies the strict clean bid invariant regarding maximum and dynamic ceiling."""
        if self.status.is_terminal or self.is_closed:
            return False
        if amount_eur <= 0:
            return False
        if amount_eur > self.max_bid_eur:
            return False
        if amount_eur > self.effective_ceiling_eur:
            return False
        return True

    def record_own_bid(self, amount_eur: int, reserve_status: ReserveStatus | None = None) -> None:
        """Records a bid placed by the agent respecting invariants."""
        if not self.can_bid_amount(amount_eur):
            raise ValueError(
                f"Puja inválida de {amount_eur} € para el lote {self.lot_id} "
                f"(máximo={self.max_bid_eur} €, techo_dinámico={self.effective_ceiling_eur} €)."
            )
        self.my_last_bid_eur = amount_eur
        self.current_bid_eur = max(self.current_bid_eur, amount_eur)
        self.is_leader = True
        self.status = AuctionStatus.LEADER
        if reserve_status is not None:
            self.reserve_status = reserve_status
        self.notes = f"Puja enviada: {amount_eur} €"

    def apply_market_snapshot(
        self,
        *,
        current_bid_eur: int,
        next_min_bid_eur: int,
        seconds_remaining: float | None,
        is_leader: bool,
        reserve_status: ReserveStatus = ReserveStatus.UNKNOWN,
        is_closed: bool = False,
        title: str | None = None,
        closing_time: datetime | None = None,
    ) -> AuctionStatus:
        """Updates lot status from a live Catawiki reading."""
        if self.status == AuctionStatus.VALIDATION_ERROR:
            return self.status

        if title and not any(phrase in title.lower() for phrase in ERROR_TITLE_PHRASES):
            self.title = title
        if closing_time is not None:
            self.closing_time = closing_time

        self.current_bid_eur = max(0, current_bid_eur)
        self.next_min_bid_eur = max(0, next_min_bid_eur)
        self.is_leader = is_leader
        if reserve_status != ReserveStatus.UNKNOWN:
            self.reserve_status = reserve_status
        self.is_closed = is_closed

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        has_future_closing = (
            self.closing_time is not None
            and self.closing_time > (now_utc + timedelta(seconds=60))
        )
        if has_future_closing and not is_closed and (seconds_remaining is None or seconds_remaining <= 0):
            effective_seconds = max(0.0, (self.closing_time - now_utc).total_seconds())
        else:
            effective_seconds = seconds_remaining

        self.seconds_remaining = effective_seconds

        should_close = is_closed or (
            effective_seconds is not None
            and effective_seconds <= 0
            and not has_future_closing
        )

        if should_close:
            self.is_closed = True
            self.seconds_remaining = 0.0
            if self.is_leader:
                if self.reserve_status == ReserveStatus.NOT_MET:
                    self.status = AuctionStatus.NOT_AWARDED_RESERVE
                    self.notes = "Subasta cerrada sin alcanzar precio de reserva"
                else:
                    self.status = AuctionStatus.WON
                    self.notes = f"Reloj adjudicado por {self.current_bid_eur} €"
            else:
                if (
                    self.current_bid_eur > self.max_bid_eur
                    or self.next_min_bid_eur > self.max_bid_eur
                ):
                    self.status = AuctionStatus.DISCARDED_MAX_EXCEEDED
                    self.notes = f"Cerrado por encima de puja máxima ({self.max_bid_eur} €)"
                elif self.status != AuctionStatus.LOST_NO_BUDGET:
                    self.status = AuctionStatus.LOST_OTHER
                    self.notes = f"Subasta cerrada en {self.current_bid_eur} €"
            return self.status

        # Auction still open
        if not self.is_leader and self.next_min_bid_eur > self.max_bid_eur:
            self.status = AuctionStatus.DISCARDED_MAX_EXCEEDED
            self.notes = (
                f"Descartado: siguiente mínimo ({self.next_min_bid_eur} €) > "
                f"máximo ({self.max_bid_eur} €)"
            )
            return self.status

        if self.is_leader:
            self.status = AuctionStatus.LEADER
            if self.my_last_bid_eur == 0 and self.current_bid_eur > 0:
                self.my_last_bid_eur = self.current_bid_eur
            if self.reserve_status == ReserveStatus.NOT_MET:
                self.notes = "Líder provisional (reserva no alcanzada)"
            else:
                self.notes = "Líder provisional"
            return self.status

        if self.my_last_bid_eur > 0 and not self.is_leader:
            self.status = AuctionStatus.OUTBID
            self.notes = f"Superado (actual: {self.current_bid_eur} €)"
            return self.status

        if effective_seconds is not None and effective_seconds <= 300:
            self.status = AuctionStatus.ASSAULT
        else:
            self.status = AuctionStatus.PATROL

        return self.status
