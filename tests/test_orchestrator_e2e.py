"""Simulación End-to-End de AuctionOrchestrator con múltiples subastas solapadas, +90s y reserva."""

import asyncio
from pathlib import Path

import pytest
from openpyxl import load_workbook

from chronobid.browser.catawiki_page import (
    CatawikiPageLoadError,
    LotPageSnapshot,
    compute_catawiki_next_min_bid,
)
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


@pytest.mark.asyncio
async def test_orchestrator_patrol_handles_transient_error_without_losing_lot(tmp_path: Path) -> None:
    excel_path = tmp_path / "cartera_resiliente.xlsx"
    url = "https://www.catawiki.com/es/l/107658893-seiko-presage"
    create_excel_template(excel_path, default_budget_eur=1000, sample_rows=[(url, 400)])

    # Establecer título inicial legítimo en el Excel
    wb = load_workbook(excel_path)
    ws = wb.active
    assert ws is not None
    ws.cell(row=7, column=3, value="Seiko - Presage - SPB045J1")
    wb.save(excel_path)
    wb.close()

    call_count = 0

    class FailingController:
        async def extract_snapshot(self) -> LotPageSnapshot:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simular error transitorio en la primera pasada
                raise CatawikiPageLoadError("Página de error detectada: 'we can't load this page right now'")
            # Segunda pasada exitosa
            return LotPageSnapshot(
                title="Seiko - Presage - SPB045J1",
                current_bid_eur=240,
                next_min_bid_eur=260,
                seconds_remaining=3600.0,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            raise NotImplementedError

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> FailingController:
        return FailingController()

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
        excel_retry_seconds=0.01,
    )

    lot = orchestrator.lots[0]
    assert lot.title == "Seiko - Presage - SPB045J1"

    # Primera pasada: debe fallar con CatawikiPageLoadError pero mantener el lote vivo
    await orchestrator.run_patrol_step()
    assert lot.status.is_active is True
    assert lot.status != AuctionStatus.LOST_OTHER
    assert lot.is_closed is False
    assert lot.title == "Seiko - Presage - SPB045J1"  # No sobreescrito por error
    assert "Error puntual de carga en Catawiki" in (lot.notes or "")

    # Segunda pasada: lectura exitosa
    await orchestrator.run_patrol_step()
    assert lot.status == AuctionStatus.PATROL
    assert lot.is_closed is False
    assert lot.current_bid_eur == 240
    assert lot.next_min_bid_eur == 260
    assert lot.title == "Seiko - Presage - SPB045J1"


@pytest.mark.asyncio
async def test_orchestrator_patrol_syncs_and_releases_leader_commitment_in_ledger_and_excel(
    tmp_path: Path,
) -> None:
    excel_path = tmp_path / "cartera_lider_patrulla.xlsx"
    url = "https://www.catawiki.com/es/l/13000013-citizen-automatic"
    create_excel_template(excel_path, default_budget_eur=1500, sample_rows=[(url, 200)])

    step = 0

    class PatrolLeaderController:
        async def extract_snapshot(self) -> LotPageSnapshot:
            nonlocal step
            step += 1
            if step == 1:
                # Ronda 1 de Patrulla: el usuario ya es líder por 1 € (faltan 594459s)
                return LotPageSnapshot(
                    title="Citizen Automatic",
                    current_bid_eur=1,
                    next_min_bid_eur=2,
                    seconds_remaining=594459.0,
                    is_leader=True,
                    reserve_status=ReserveStatus.NO_RESERVE,
                    is_closed=False,
                )
            # Ronda 2 de Patrulla: otro postor supera la puja a 5 € durante la patrulla
            return LotPageSnapshot(
                title="Citizen Automatic",
                current_bid_eur=5,
                next_min_bid_eur=6,
                seconds_remaining=590000.0,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            raise NotImplementedError

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> PatrolLeaderController:
        return PatrolLeaderController()

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
        excel_retry_seconds=0.01,
    )

    lot = orchestrator.lots[0]

    # Ronda 1: debe detectar LIDER (1 €) y reflejar 1 € en Comprometido y 1499 € en Disponible
    await orchestrator.run_patrol_step()
    await orchestrator.sync_worker.flush_once()

    assert lot.status == AuctionStatus.LEADER
    assert lot.my_last_bid_eur == 1
    assert lot.current_bid_eur == 1
    assert lot.effective_ceiling_eur == 200
    assert orchestrator.ledger.committed_leader_eur == 1
    assert orchestrator.ledger.available_eur == 1499

    wb = load_workbook(excel_path, data_only=True)
    ws = wb.active
    assert ws is not None
    assert ws["B3"].value == 1
    assert ws["B4"].value == 1499
    assert ws["E7"].value == "LIDER"
    assert ws["F7"].value == 1
    wb.close()

    # Ronda 2: otro postor supera la puja en patrulla -> debe liberar el 1 € al instante
    await orchestrator.run_patrol_step()
    await orchestrator.sync_worker.flush_once()

    assert lot.status == AuctionStatus.OUTBID
    assert orchestrator.ledger.committed_leader_eur == 0
    assert orchestrator.ledger.available_eur == 1500

    wb2 = load_workbook(excel_path, data_only=True)
    ws2 = wb2.active
    assert ws2 is not None
    assert ws2["B3"].value == 0
    assert ws2["B4"].value == 1500
    assert ws2["E7"].value == "SUPERADO"
    wb2.close()


async def test_orchestrator_unconfirmed_bid_releases_budget_without_false_outbid(
    tmp_path: Path,
) -> None:
    """
    Si submit_minimum_bid devuelve un snapshot donde la puja no llegó a confirmarse
    (is_leader=False y current_bid_eur < bid_amt), el orquestador:
      1. No debe registrar lot.my_last_bid_eur ni marcar falsamente el lote como SUPERADO.
      2. Debe liberar de inmediato la reserva en BudgetLedger.
      3. Puede reintentar la puja en el siguiente tick de asalto y ganarla cuando se confirma.
    """
    excel_path = tmp_path / "cartera_unconfirmed_bid.xlsx"
    url = "https://www.catawiki.com/es/l/900099-opening-lot"

    create_excel_template(
        excel_path,
        default_budget_eur=500,
        sample_rows=[(url, 100)],
    )

    state_after_first_attempt: dict[str, object] = {}

    class UnconfirmedThenConfirmedController:
        def __init__(self, target_lot: WatchLot, orchestrator_ref: list[AuctionOrchestrator]) -> None:
            self.lot = target_lot
            self.orchestrator_ref = orchestrator_ref
            self.extract_calls = 0
            self.submit_calls = 0
            self.confirmed = False

        async def extract_snapshot(self) -> LotPageSnapshot:
            self.extract_calls += 1
            if self.extract_calls == 2:
                # Justo al inicio del tick 2, capturamos cómo quedó el lote tras el intento fallido del tick 1
                orch = self.orchestrator_ref[0]
                state_after_first_attempt["my_last_bid_eur"] = self.lot.my_last_bid_eur
                state_after_first_attempt["status"] = self.lot.status
                state_after_first_attempt["committed_eur"] = orch.ledger.committed_leader_eur
                state_after_first_attempt["notes"] = self.lot.notes

            if not self.confirmed:
                return LotPageSnapshot(
                    title="Tissot Opening Lot",
                    current_bid_eur=0,
                    next_min_bid_eur=1,
                    seconds_remaining=18.0 if self.extract_calls == 1 else 12.0,
                    is_leader=False,
                    reserve_status=ReserveStatus.NO_RESERVE,
                    is_closed=False,
                )

            return LotPageSnapshot(
                title="Tissot Opening Lot",
                current_bid_eur=1,
                next_min_bid_eur=2,
                seconds_remaining=0.0,
                is_leader=True,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=True,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            self.submit_calls += 1
            if self.submit_calls == 1:
                # Primer intento: el modal de confirmación no llegó a aceptarse en Catawiki
                return LotPageSnapshot(
                    title="Tissot Opening Lot",
                    current_bid_eur=0,
                    next_min_bid_eur=1,
                    seconds_remaining=15.0,
                    is_leader=False,
                    reserve_status=ReserveStatus.NO_RESERVE,
                    is_closed=False,
                )

            # Segundo intento: la confirmación se completa y pasamos a ser líderes con 1 €
            self.confirmed = True
            return LotPageSnapshot(
                title="Tissot Opening Lot",
                current_bid_eur=1,
                next_min_bid_eur=2,
                seconds_remaining=10.0,
                is_leader=True,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def close(self) -> None:
            pass

    orchestrator_holder: list[AuctionOrchestrator] = []

    async def controller_factory(lot: WatchLot) -> UnconfirmedThenConfirmedController:
        return UnconfirmedThenConfirmedController(lot, orchestrator_holder)

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
        assault_threshold_seconds=300.0,
        patrol_interval_seconds=0.01,
        assault_tick_seconds=0.005,
        snipe_window_seconds=25.0,
        excel_retry_seconds=0.01,
    )
    orchestrator_holder.append(orchestrator)

    lot = orchestrator.lots[0]
    lot.seconds_remaining = 20.0
    lot.status = AuctionStatus.ASSAULT

    await orchestrator._run_assault_for_lot(lot)
    await orchestrator.sync_worker.flush_once()

    # Verificaciones tras el primer intento no confirmado
    assert state_after_first_attempt["my_last_bid_eur"] == 0
    assert state_after_first_attempt["status"] == AuctionStatus.ASSAULT
    assert state_after_first_attempt["status"] != AuctionStatus.OUTBID
    assert state_after_first_attempt["committed_eur"] == 0
    assert "no confirmada" in str(state_after_first_attempt["notes"])

    # Verificaciones tras el reintento confirmado y cierre de la subasta
    assert lot.my_last_bid_eur == 1
    assert lot.current_bid_eur == 1
    assert lot.status == AuctionStatus.WON
    assert orchestrator.ledger.spent_eur == 1


@pytest.mark.asyncio
async def test_orchestrator_patrol_interval_independent_from_concurrent_assault(tmp_path: Path) -> None:
    """
    Verifica que la existencia de un lote en seguimiento continuo de Modo Asalto
    no acelera las consultas de los lotes en Modo Patrulla, manteniéndolas en su
    intervalo espaciado independiente.
    """
    excel_path = tmp_path / "cartera_mixta.xlsx"
    url_assault = "https://www.catawiki.com/es/l/900010-reloj-asalto"
    url_patrol = "https://www.catawiki.com/es/l/900020-reloj-patrulla"

    create_excel_template(
        excel_path,
        default_budget_eur=1000,
        sample_rows=[
            (url_assault, 300),
            (url_patrol, 400),
        ],
    )

    patrol_query_count = 0
    assault_query_count = 0

    class DualRoomController:
        def __init__(self, is_assault: bool) -> None:
            self.is_assault = is_assault
            self.ticks = 0

        async def extract_snapshot(self) -> LotPageSnapshot:
            nonlocal patrol_query_count, assault_query_count
            self.ticks += 1
            if self.is_assault:
                assault_query_count += 1
                is_closed = self.ticks >= 6
                return LotPageSnapshot(
                    title="Reloj Asalto",
                    current_bid_eur=100,
                    next_min_bid_eur=110,
                    seconds_remaining=0.0 if is_closed else 15.0,
                    is_leader=True,
                    reserve_status=ReserveStatus.NO_RESERVE,
                    is_closed=is_closed,
                )
            else:
                patrol_query_count += 1
                return LotPageSnapshot(
                    title="Reloj Patrulla",
                    current_bid_eur=200,
                    next_min_bid_eur=220,
                    seconds_remaining=3600.0,
                    is_leader=False,
                    reserve_status=ReserveStatus.NO_RESERVE,
                    is_closed=False,
                )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            return await self.extract_snapshot()

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> DualRoomController:
        is_assault = "reloj-asalto" in lot.url
        return DualRoomController(is_assault)

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
        assault_threshold_seconds=300.0,
        patrol_interval_seconds=1.0,  # 1.0s para patrulla
        assault_tick_seconds=0.01,    # 10 ms para asalto
        snipe_window_seconds=25.0,
        excel_retry_seconds=0.01,
    )

    run_task = asyncio.create_task(orchestrator.run_until_complete())

    lot_assault, lot_patrol = orchestrator.lots
    for _ in range(50):
        if lot_assault.is_closed or not lot_assault.status.is_active:
            break
        await asyncio.sleep(0.02)

    orchestrator.request_stop()
    await run_task

    # Verificaciones:
    # 1. El reloj en asalto se consultó varias veces a alta frecuencia y finalizó ganado
    assert assault_query_count >= 5
    assert lot_assault.status == AuctionStatus.WON

    # 2. El reloj en patrulla solo se consultó 1 vez en la ronda inicial, no en bucle continuo
    assert patrol_query_count == 1
    assert lot_patrol.status == AuctionStatus.PATROL


def test_orchestrator_calculate_patrol_wait_seconds_adaptive(tmp_path: Path) -> None:
    """
    Verifica que _calculate_patrol_wait_seconds() se adapta al tiempo restante
    hacia el umbral de 300s cuando algún lote está próximo a conmutar a asalto.
    """
    excel_path = tmp_path / "cartera_wait.xlsx"
    url1 = "https://www.catawiki.com/es/l/900001-near-assault"
    url2 = "https://www.catawiki.com/es/l/900002-far-patrol"
    create_excel_template(excel_path, default_budget_eur=1000, sample_rows=[(url1, 200), (url2, 300)])

    orchestrator = AuctionOrchestrator(
        excel_path,
        assault_threshold_seconds=300.0,
        patrol_interval_seconds=15.0,
    )
    lot1, lot2 = orchestrator.lots
    lot1.seconds_remaining = 304.0  # 4 segundos antes de entrar en asalto (300s)
    lot2.seconds_remaining = 1800.0

    wait = orchestrator._calculate_patrol_wait_seconds()
    assert wait == 4.0

    # Si todos los relojes están lejos (> 15s del umbral), espera los 15s estándar
    lot1.seconds_remaining = 500.0
    wait_far = orchestrator._calculate_patrol_wait_seconds()
    assert wait_far == 15.0

