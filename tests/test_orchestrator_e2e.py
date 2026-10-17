"""Simulación End-to-End de AuctionOrchestrator con múltiples subastas solapadas, +90s y reserva."""

from pathlib import Path

from openpyxl import load_workbook

from chronobid.browser.catawiki_page import LotPageSnapshot, compute_catawiki_next_min_bid
from chronobid.excel.workbook_io import create_excel_template
from chronobid.models.auction import AuctionStatus, ReserveStatus, WatchLot
from chronobid.orchestrator import AuctionOrchestrator


class SimulatedLiveAuctionRoom:
    """
    Simulador determinista por ticks de una subasta en Catawiki con:
      - Cuenta atrás que decrece en cada lectura.
      - Rival automático hasta `rival_max_bid_eur` que extiende el reloj +90s al contraatacar.
      - Precio de reserva oculto `reserve_price_eur` que requiere escalera para desbloquearse.
    """

    def __init__(
        self,
        *,
        title: str,
        initial_bid_eur: int,
        initial_seconds: float,
        rival_max_bid_eur: int = 0,
        reserve_price_eur: int = 0,
        tick_decrement_seconds: float = 12.0,
    ) -> None:
        self.title = title
        self.current_bid_eur = initial_bid_eur
        self.seconds_remaining = initial_seconds
        self.rival_max_bid_eur = rival_max_bid_eur
        self.reserve_price_eur = reserve_price_eur
        self.tick_decrement_seconds = tick_decrement_seconds
        self.agent_is_leader = False
        self.is_closed = False
        self.agent_bids_history: list[int] = []
        self.overtime_extensions: int = 0

    @property
    def reserve_status(self) -> ReserveStatus:
        if self.reserve_price_eur <= 0:
            return ReserveStatus.NO_RESERVE
        if self.current_bid_eur >= self.reserve_price_eur:
            return ReserveStatus.MET
        return ReserveStatus.NOT_MET

    @property
    def next_min_bid_eur(self) -> int:
        return compute_catawiki_next_min_bid(self.current_bid_eur)

    async def extract_snapshot(self) -> LotPageSnapshot:
        if not self.is_closed:
            # Si el agente es líder con la reserva alcanzada (o sin reserva) y ningún rival
            # puede superar la puja, el tiempo restante se agota y la subasta cierra.
            if (
                self.agent_is_leader
                and self.reserve_status.is_satisfied
                and self.next_min_bid_eur > self.rival_max_bid_eur
            ):
                self.seconds_remaining = max(
                    0.0, self.seconds_remaining - self.tick_decrement_seconds
                )
            else:
                self.seconds_remaining = max(
                    0.0, self.seconds_remaining - self.tick_decrement_seconds
                )

            if self.seconds_remaining <= 0:
                self.is_closed = True

        return self._current_snapshot()

    async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
        self.agent_bids_history.append(bid_amount_eur)
        self.current_bid_eur = bid_amount_eur
        self.agent_is_leader = True

        # Regla de Catawiki: puja en los últimos 60s extiende/reinicia en +90s si un rival responde
        next_step = self.next_min_bid_eur
        if next_step <= self.rival_max_bid_eur:
            # El rival contraataca dentro de la prórroga de +90s
            self.current_bid_eur = next_step
            self.agent_is_leader = False
            self.seconds_remaining = 90.0
            self.overtime_extensions += 1
        elif self.reserve_status == ReserveStatus.NOT_MET:
            # Sigue faltando desbloquear la reserva en el siguiente tick (< 25s)
            self.seconds_remaining = min(self.seconds_remaining, 18.0)

        return self._current_snapshot()

    def _current_snapshot(self) -> LotPageSnapshot:
        return LotPageSnapshot(
            title=self.title,
            current_bid_eur=self.current_bid_eur,
            next_min_bid_eur=self.next_min_bid_eur,
            seconds_remaining=self.seconds_remaining,
            is_leader=self.agent_is_leader,
            reserve_status=self.reserve_status,
            is_closed=self.is_closed,
        )

    async def close(self) -> None:
        return None


async def test_orchestrator_e2e_overlapping_auctions_reserve_ladder_and_portfolio_maximization(
    tmp_path: Path,
) -> None:
    """
    Simulación E2E completa con 4 relojes y un Presupuesto Global compartido de 700 €:
      - Reloj 1 (Omega): M_1 = 500 €, arranca en 200 €, un rival empuja hasta 520 € (> M_1).
        El agente puja en < 25s, entra en prórroga +90s y cuando el rival supera 500 €
        abandona y libera el presupuesto al instante.
      - Reloj 2 (Seiko): M_2 = 300 €, arranca en 160 €, sin rival pero con Precio de Reserva
        oculto en 220 €. El agente entra en < 25s (170 €) y sube escalón a escalón
        (190 € -> 200 € -> 220 €) deteniéndose exactamente al desbloquear la reserva en 220 €.
      - Reloj 3 (Tissot): M_3 = 280 €, arranca en 180 €, un rival puja hasta 220 €, el agente
        contraataca en prórroga (+90s) con 240 € y gana el reloj por 240 €.
      - Reloj 4 (Longines): M_4 = 350 €, arranca en 220 €; como el saldo disponible restante
        tras ganar Reloj 2 (220 €) y Reloj 3 (240 €) es 700 - 460 = 240 €, y el rival sube
        el lote a 260 € (> 240 € disponible), el agente respeta el invariante de presupuesto
        global y termina en PERDIDO: Sin Presupuesto sin exceder jamás los 700 €.
    """
    excel_path = tmp_path / "cartera_e2e.xlsx"
    url_1 = "https://www.catawiki.com/es/l/900001-omega"
    url_2 = "https://www.catawiki.com/es/l/900002-seiko"
    url_3 = "https://www.catawiki.com/es/l/900003-tissot"
    url_4 = "https://www.catawiki.com/es/l/900004-longines"

    create_excel_template(
        excel_path,
        default_budget_eur=700,
        sample_rows=[
            (url_1, 300),
            (url_2, 250),
            (url_3, 250),
            (url_4, 400),
        ],
    )

    rooms: dict[str, SimulatedLiveAuctionRoom] = {
        url_1: SimulatedLiveAuctionRoom(
            title="Omega Seamaster 1968",
            initial_bid_eur=240,
            initial_seconds=30.0,
            rival_max_bid_eur=320,  # Superará el máximo de 300 € tras prórroga +90s
            reserve_price_eur=0,
            tick_decrement_seconds=10.0,
        ),
        url_2: SimulatedLiveAuctionRoom(
            title="Seiko Chronograph Automatic",
            initial_bid_eur=150,
            initial_seconds=30.0,
            rival_max_bid_eur=0,
            reserve_price_eur=190,  # Reserva oculta en 190 €
            tick_decrement_seconds=4.0,
        ),
        url_3: SimulatedLiveAuctionRoom(
            title="Tissot PRX Powermatic",
            initial_bid_eur=160,
            initial_seconds=35.0,
            rival_max_bid_eur=180,  # Provocará prórroga +90s y se rendirá ante 190 €
            reserve_price_eur=0,
            tick_decrement_seconds=15.0,
        ),
        url_4: SimulatedLiveAuctionRoom(
            title="Longines Flagship Heritage",
            initial_bid_eur=310,
            initial_seconds=150.0,  # Cierra después de los relojes 2 y 3 (disponible = 320 < siguiente mín 330)
            rival_max_bid_eur=350,
            reserve_price_eur=0,
            tick_decrement_seconds=15.0,
        ),
    }

    async def sim_controller_factory(lot: WatchLot) -> SimulatedLiveAuctionRoom:
        return rooms[lot.url]

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=sim_controller_factory,
        assault_threshold_seconds=300.0,
        patrol_interval_seconds=0.01,
        assault_tick_seconds=0.005,
        snipe_window_seconds=25.0,
        excel_retry_seconds=0.02,
    )

    await orchestrator.run_until_complete()

    lot1, lot2, lot3, lot4 = orchestrator.lots

    # Reloj 1: superó su puja máxima tras prórroga y liberó todo su presupuesto
    assert lot1.status == AuctionStatus.DISCARDED_MAX_EXCEEDED
    assert rooms[url_1].overtime_extensions >= 1

    # Reloj 2: ganó desbloqueando la reserva paso a paso exactamente en 190 € (< 250 € máx)
    assert lot2.status == AuctionStatus.WON
    assert lot2.current_bid_eur == 190
    assert rooms[url_2].agent_bids_history == [160, 170, 180, 190]

    # Reloj 3: ganó tras contraatacar en la prórroga de +90s en 190 €
    assert lot3.status == AuctionStatus.WON
    assert lot3.current_bid_eur == 190
    assert rooms[url_3].overtime_extensions >= 1

    # Reloj 4: no pudo pujar 330 € porque el saldo disponible restante era 700 - 380 = 320 €
    assert lot4.status == AuctionStatus.LOST_NO_BUDGET

    # Contabilidad final del Ledger
    assert orchestrator.ledger.spent_eur == 380
    assert orchestrator.ledger.committed_leader_eur == 0
    assert orchestrator.ledger.available_eur == 320

    # Verificar que todo quedó fielmente reflejado en el archivo Excel final
    wb = load_workbook(excel_path, data_only=True)
    ws = wb.active
    assert ws is not None
    assert ws["B1"].value == 700
    assert ws["B2"].value == 380
    assert ws["B3"].value == 0
    assert ws["B4"].value == 320

    assert ws["E7"].value == "PERDIDO: Superó Máximo"
    assert ws["E8"].value == "GANADO"
    assert ws["G8"].value == 190
    assert ws["H8"].value == "ALCANZADA"
    assert ws["E9"].value == "GANADO"
    assert ws["G9"].value == 190
    assert ws["E10"].value == "PERDIDO: Sin Presupuesto"
    wb.close()
