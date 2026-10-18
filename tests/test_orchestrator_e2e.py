"""End-to-End simulation of AuctionOrchestrator with multiple overlapping auctions, +90s overtime, and reserve price."""

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
    Deterministic tick-based simulator for a Catawiki auction with:
      - Countdown decreasing on each reading.
      - Automated rival up to `rival_max_bid_eur` that extends the clock +90s when counterattacking.
      - Hidden reserve price `reserve_price_eur` that requires a ladder to unlock.
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
            # If the agent is leader with reserve met (or no reserve) and no rival
            # can outbid, remaining time runs out and the auction closes.
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

        # Catawiki rule: bid in the last 60s extends/resets to +90s if a rival responds
        next_step = self.next_min_bid_eur
        if next_step <= self.rival_max_bid_eur:
            # The rival counterattacks within the +90s overtime
            self.current_bid_eur = next_step
            self.agent_is_leader = False
            self.seconds_remaining = 90.0
            self.overtime_extensions += 1
        elif self.reserve_status == ReserveStatus.NOT_MET:
            # Still needs reserve unlocking in the next tick (< 25s)
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
    Complete E2E simulation with 4 watches and a shared Global Budget of 700 €:
      - Watch 1 (Omega): M_1 = 500 €, starts at 200 €, a rival pushes to 520 € (> M_1).
        The agent bids at < 25s, enters +90s overtime, and when the rival exceeds 500 €
        abandons and releases budget immediately.
      - Watch 2 (Seiko): M_2 = 300 €, starts at 160 €, no rival but with hidden Reserve Price
        at 220 €. The agent enters at < 25s (170 €) and climbs step by step
        (190 € -> 200 € -> 220 €), stopping exactly upon unlocking the reserve at 220 €.
      - Watch 3 (Tissot): M_3 = 280 €, starts at 180 €, a rival bids up to 220 €, the agent
        counterattacks in overtime (+90s) with 240 € and wins the watch for 240 €.
      - Watch 4 (Longines): M_4 = 350 €, starts at 220 €; since remaining available balance
        after winning Watch 2 (220 €) and Watch 3 (240 €) is 700 - 460 = 240 €, and the rival raises
        the lot to 260 € (> 240 € available), the agent respects the global budget invariant
        and finishes in PERDIDO: Sin Presupuesto without ever exceeding 700 €.
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
            rival_max_bid_eur=320,  # Will exceed max of 300 € after +90s overtime
            reserve_price_eur=0,
            tick_decrement_seconds=10.0,
        ),
        url_2: SimulatedLiveAuctionRoom(
            title="Seiko Chronograph Automatic",
            initial_bid_eur=150,
            initial_seconds=30.0,
            rival_max_bid_eur=0,
            reserve_price_eur=190,  # Hidden reserve at 190 €
            tick_decrement_seconds=4.0,
        ),
        url_3: SimulatedLiveAuctionRoom(
            title="Tissot PRX Powermatic",
            initial_bid_eur=160,
            initial_seconds=35.0,
            rival_max_bid_eur=180,  # Will cause +90s overtime and surrender to 190 €
            reserve_price_eur=0,
            tick_decrement_seconds=15.0,
        ),
        url_4: SimulatedLiveAuctionRoom(
            title="Longines Flagship Heritage",
            initial_bid_eur=310,
            initial_seconds=150.0,  # Closes after watches 2 and 3 (available = 320 < next min 330)
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

    # Watch 1: exceeded max bid after overtime and released all its budget
    assert lot1.status == AuctionStatus.DISCARDED_MAX_EXCEEDED
    assert rooms[url_1].overtime_extensions >= 1

    # Watch 2: won by unlocking reserve step by step at exactly 190 € (< 250 € max)
    assert lot2.status == AuctionStatus.WON
    assert lot2.current_bid_eur == 190
    assert rooms[url_2].agent_bids_history == [160, 170, 180, 190]

    # Watch 3: won after counterattacking in +90s overtime at 190 €
    assert lot3.status == AuctionStatus.WON
    assert lot3.current_bid_eur == 190
    assert rooms[url_3].overtime_extensions >= 1

    # Watch 4: could not bid 330 € because remaining available balance was 700 - 380 = 320 €
    assert lot4.status == AuctionStatus.LOST_NO_BUDGET

    # Final Ledger accounting
    assert orchestrator.ledger.spent_eur == 380
    assert orchestrator.ledger.committed_leader_eur == 0
    assert orchestrator.ledger.available_eur == 320

    # Verify everything was faithfully reflected in final Excel file
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

    # Set legitimate initial title in Excel
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
                # Simulate transient error on first pass
                raise CatawikiPageLoadError("Página de error detectada: 'we can't load this page right now'")
            # Second pass succeeds
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

    # First pass: must fail with CatawikiPageLoadError but keep lot alive
    await orchestrator.run_patrol_step()
    assert lot.status.is_active is True
    assert lot.status != AuctionStatus.LOST_OTHER
    assert lot.is_closed is False
    assert lot.title == "Seiko - Presage - SPB045J1"  # Not overwritten by error
    assert "Error puntual de carga en Catawiki" in (lot.notes or "")

    # Second pass: successful read
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
                # Patrol Round 1: user is already leader for 1 € (594459s remaining)
                return LotPageSnapshot(
                    title="Citizen Automatic",
                    current_bid_eur=1,
                    next_min_bid_eur=2,
                    seconds_remaining=594459.0,
                    is_leader=True,
                    reserve_status=ReserveStatus.NO_RESERVE,
                    is_closed=False,
                )
            # Patrol Round 2: another bidder outbids to 5 € during patrol
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

    # Round 1: must detect LEADER (1 €) and reflect 1 € committed and 1499 € available
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

    # Round 2: another bidder outbids in patrol -> must release 1 € immediately
    await orchestrator.run_patrol_step(force=True)
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
    If submit_minimum_bid returns a snapshot where the bid was not confirmed
    (is_leader=False and current_bid_eur < bid_amt), the orchestrator:
      1. Must not record lot.my_last_bid_eur nor falsely mark lot as OUTBID.
      2. Must immediately release reservation in BudgetLedger.
      3. Can retry the bid on next assault tick and win it when confirmed.
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
                # Right at tick 2 start, capture lot state after failed attempt in tick 1
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
                # First attempt: confirmation modal was not accepted on Catawiki
                return LotPageSnapshot(
                    title="Tissot Opening Lot",
                    current_bid_eur=0,
                    next_min_bid_eur=1,
                    seconds_remaining=15.0,
                    is_leader=False,
                    reserve_status=ReserveStatus.NO_RESERVE,
                    is_closed=False,
                )

            # Second attempt: confirmation completes and we become leader with 1 €
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

    # Verifications after first unconfirmed attempt
    assert state_after_first_attempt["my_last_bid_eur"] == 0
    assert state_after_first_attempt["status"] == AuctionStatus.ASSAULT
    assert state_after_first_attempt["status"] != AuctionStatus.OUTBID
    assert state_after_first_attempt["committed_eur"] == 0
    assert "no confirmada" in str(state_after_first_attempt["notes"])

    # Verifications after confirmed retry and auction close
    assert lot.my_last_bid_eur == 1
    assert lot.current_bid_eur == 1
    assert lot.status == AuctionStatus.WON
    assert orchestrator.ledger.spent_eur == 1


@pytest.mark.asyncio
async def test_orchestrator_patrol_interval_independent_from_concurrent_assault(tmp_path: Path) -> None:
    """
    Verify that the existence of a lot in continuous Assault Mode tracking
    does not accelerate queries for lots in Patrol Mode, keeping them in their
    independent spaced interval.
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
        patrol_interval_seconds=1.0,  # 1.0s for patrol
        assault_tick_seconds=0.01,    # 10 ms for assault
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

    # Verifications:
    # 1. The assault watch was queried multiple times at high frequency and ended as won
    assert assault_query_count >= 5
    assert lot_assault.status == AuctionStatus.WON

    # 2. The patrol watch was only queried once in the initial round, not in a tight loop
    assert patrol_query_count == 1
    assert lot_patrol.status == AuctionStatus.PATROL


def test_orchestrator_calculate_patrol_wait_seconds_adaptive(tmp_path: Path) -> None:
    """
    Verify that _calculate_patrol_wait_seconds() adapts to remaining time
    towards the 300s threshold when a lot is close to switching to assault.
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
    lot1.seconds_remaining = 304.0  # 4 seconds before entering assault (300s)
    lot2.seconds_remaining = 1800.0

    wait = orchestrator._calculate_patrol_wait_seconds()
    assert wait == 4.0

    # If all watches are far (> 15s from threshold), waits standard 15s
    lot1.seconds_remaining = 500.0
    wait_far = orchestrator._calculate_patrol_wait_seconds()
    assert wait_far == 15.0


@pytest.mark.asyncio
async def test_orchestrator_dynamic_excel_budget_and_max_bid_update(tmp_path: Path) -> None:
    """
    Verify that modifying global budget (B1) and clean max bid (column B)
    in Excel during execution hot-reloads Ledger and dynamic ceilings.
    """
    excel_path = tmp_path / "cartera_live_budget.xlsx"
    url = "https://www.catawiki.com/es/l/950001-omega-speedmaster"
    create_excel_template(excel_path, default_budget_eur=1000, sample_rows=[(url, 500)])

    class DummyController:
        async def extract_snapshot(self) -> LotPageSnapshot:
            return LotPageSnapshot(
                title="Omega Speedmaster",
                current_bid_eur=200,
                next_min_bid_eur=220,
                seconds_remaining=1000.0,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            return await self.extract_snapshot()

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> DummyController:
        return DummyController()

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
        excel_poll_interval_seconds=0.01,
        patrol_interval_seconds=1.0,
    )
    assert orchestrator.ledger.global_budget_eur == 1000
    assert orchestrator.lots[0].max_bid_eur == 500

    # Run initial patrol
    await orchestrator.run_patrol_step()
    assert orchestrator.lots[0].status == AuctionStatus.PATROL

    # Modify Excel on disk: B1 = 2500, max bid in B7 = 750
    wb = load_workbook(excel_path)
    ws = wb.active
    ws["B1"].value = 2500
    ws["B7"].value = 750
    wb.save(excel_path)
    wb.close()

    # Check hot reload
    reloaded = await orchestrator._check_and_reload_excel()
    assert reloaded is True
    assert orchestrator.ledger.global_budget_eur == 2500
    assert orchestrator.ledger.available_eur == 2500
    assert orchestrator.lots[0].max_bid_eur == 750
    assert orchestrator.lots[0].effective_ceiling_eur == 750


@pytest.mark.asyncio
async def test_orchestrator_dynamic_excel_revive_discarded_lot(tmp_path: Path) -> None:
    """
    Verify that if a lot was discarded for exceeding previous cap (DISCARDED_MAX_EXCEEDED),
    increasing clean max bid in Excel automatically reactivates the lot.
    """
    excel_path = tmp_path / "cartera_revive.xlsx"
    url = "https://www.catawiki.com/es/l/950002-rolex-submariner"
    create_excel_template(excel_path, default_budget_eur=2000, sample_rows=[(url, 300)])

    class DiscardController:
        async def extract_snapshot(self) -> LotPageSnapshot:
            return LotPageSnapshot(
                title="Rolex Submariner",
                current_bid_eur=350,
                next_min_bid_eur=380,  # 380 > 300 -> gets discarded
                seconds_remaining=800.0,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            return await self.extract_snapshot()

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> DiscardController:
        return DiscardController()

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
        excel_poll_interval_seconds=0.01,
    )
    await orchestrator.run_patrol_step()
    lot = orchestrator.lots[0]
    assert lot.status == AuctionStatus.DISCARDED_MAX_EXCEEDED

    # User edits Excel and raises cap to 450 € (>= 380 €)
    wb = load_workbook(excel_path)
    ws = wb.active
    ws["B7"].value = 450
    wb.save(excel_path)
    wb.close()

    changed = await orchestrator._check_and_reload_excel()
    assert changed is True
    assert lot.max_bid_eur == 450
    assert lot.status == AuctionStatus.PATROL
    assert "Reactivado" in lot.notes


@pytest.mark.asyncio
async def test_orchestrator_dynamic_excel_add_and_remove_lot(tmp_path: Path) -> None:
    """
    Verify that adding a new row in Excel enrolls lot in PENDING and patrols it,
    and deleting a row removes it from tracking and cancels its tasks.
    """
    excel_path = tmp_path / "cartera_add_remove.xlsx"
    url1 = "https://www.catawiki.com/es/l/950003-tag-heuer"
    url2 = "https://www.catawiki.com/es/l/950004-breitling-navitimer"
    create_excel_template(excel_path, default_budget_eur=3000, sample_rows=[(url1, 400)])

    visited_lots: list[str] = []

    class TrackController:
        def __init__(self, lot_id: str) -> None:
            self.lot_id = lot_id

        async def extract_snapshot(self) -> LotPageSnapshot:
            visited_lots.append(self.lot_id)
            return LotPageSnapshot(
                title=f"Watch {self.lot_id}",
                current_bid_eur=100,
                next_min_bid_eur=110,
                seconds_remaining=500.0,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            return await self.extract_snapshot()

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> TrackController:
        return TrackController(lot.lot_id)

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
        excel_poll_interval_seconds=0.01,
        patrol_interval_seconds=0.05,
    )
    assert len(orchestrator.lots) == 1

    # 1. Add new watch in row 8
    wb = load_workbook(excel_path)
    ws = wb.active
    ws["A8"].value = url2
    ws["B8"].value = 600
    wb.save(excel_path)
    wb.close()

    changed = await orchestrator._check_and_reload_excel()
    assert changed is True
    assert len(orchestrator.lots) == 2
    assert orchestrator.lots[1].url == url2
    assert orchestrator.lots[1].max_bid_eur == 600
    assert orchestrator.lots[1].status == AuctionStatus.PENDING

    # Patrol to ensure new lot is processed
    await orchestrator.run_patrol_step()
    assert any("950004" in v for v in visited_lots)
    assert orchestrator.lots[1].status == AuctionStatus.PATROL

    # 2. Delete first watch (row 7) in Excel
    wb = load_workbook(excel_path)
    ws = wb.active
    ws["A7"].value = None
    ws["B7"].value = None
    wb.save(excel_path)
    wb.close()

    changed = await orchestrator._check_and_reload_excel()
    assert changed is True
    assert len(orchestrator.lots) == 1
    assert orchestrator.lots[0].url == url2


@pytest.mark.asyncio
async def test_orchestrator_dynamic_excel_ignores_internal_writes(tmp_path: Path) -> None:
    """
    Verify that periodic ChronoBid output writes (columns C:J and B2:B4)
    do not trigger spurious hot reloads thanks to input signature.
    """
    excel_path = tmp_path / "cartera_no_retrigger.xlsx"
    url = "https://www.catawiki.com/es/l/950005-cartier-santos"
    create_excel_template(excel_path, default_budget_eur=1500, sample_rows=[(url, 800)])

    orchestrator = AuctionOrchestrator(
        excel_path,
        excel_poll_interval_seconds=0.01,
    )

    # Modify only output columns (B2 spent, C7 title, E7 status)
    wb = load_workbook(excel_path)
    ws = wb.active
    ws["B2"].value = 200
    ws["C7"].value = "Cartier Santos Automático"
    ws["E7"].value = "PATRULLA"
    wb.save(excel_path)
    wb.close()

    # _check_and_reload_excel must detect that inputs (B1, A:B) did not change
    changed = await orchestrator._check_and_reload_excel()
    assert changed is False


@pytest.mark.asyncio
async def test_orchestrator_dynamic_excel_tolerates_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Verify that if Excel file is transiently locked (PermissionError / OSError),
    _check_and_reload_excel handles the error without raising an exception and retries on next poll.
    """
    excel_path = tmp_path / "cartera_lock.xlsx"
    url = "https://www.catawiki.com/es/l/950006-zenith-el-primero"
    create_excel_template(excel_path, default_budget_eur=2000, sample_rows=[(url, 900)])

    orchestrator = AuctionOrchestrator(
        excel_path,
        excel_poll_interval_seconds=0.01,
    )

    # Modify file on disk
    wb = load_workbook(excel_path)
    ws = wb.active
    ws["B1"].value = 3500
    wb.save(excel_path)
    wb.close()

    # Simulate Windows lock in extract_input_signature
    import chronobid.orchestrator as orch_module

    def mock_extract(path):
        return None  # Returns None as happens on PermissionError

    monkeypatch.setattr(orch_module, "extract_input_signature", mock_extract)

    changed = await orchestrator._check_and_reload_excel()
    assert changed is False
    assert orchestrator.ledger.global_budget_eur == 2000

    # Remove mock: next poll should reconcile properly
    monkeypatch.undo()
    changed_retry = await orchestrator._check_and_reload_excel()
    assert changed_retry is True
    assert orchestrator.ledger.global_budget_eur == 3500


@pytest.mark.asyncio
async def test_orchestrator_run_until_complete_live_reload_wakes_patrol(tmp_path: Path) -> None:
    """
    Verify that adding a watch to Excel during run_until_complete()
    wakes patrol wait immediately via _reload_event
    without waiting for full 10-second interval.
    """
    excel_path = tmp_path / "cartera_wake.xlsx"
    url1 = "https://www.catawiki.com/es/l/950010-watch-one"
    url2 = "https://www.catawiki.com/es/l/950011-watch-two"
    create_excel_template(excel_path, default_budget_eur=2000, sample_rows=[(url1, 500)])

    queried_lots: list[str] = []

    class FastController:
        def __init__(self, lot: WatchLot) -> None:
            self.lot = lot

        async def extract_snapshot(self) -> LotPageSnapshot:
            queried_lots.append(self.lot.url)
            return LotPageSnapshot(
                title=f"Watch {self.lot.lot_id}",
                current_bid_eur=100,
                next_min_bid_eur=110,
                seconds_remaining=1000.0,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            return await self.extract_snapshot()

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> FastController:
        return FastController(lot)

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
        excel_poll_interval_seconds=0.02,
        patrol_interval_seconds=10.0,  # 10s wait
    )

    run_task = asyncio.create_task(orchestrator.run_until_complete())

    # Wait for first patrol of watch 1
    for _ in range(50):
        if url1 in queried_lots:
            break
        await asyncio.sleep(0.01)
    assert url1 in queried_lots
    assert url2 not in queried_lots

    # Now orchestrator is waiting in 10s patrol wait.
    # Modify Excel by adding watch 2
    wb = load_workbook(excel_path)
    ws = wb.active
    ws["A8"].value = url2
    ws["B8"].value = 600
    wb.save(excel_path)
    wb.close()

    # Wait very little time (much less than 10s of patrol_interval_seconds)
    for _ in range(50):
        if url2 in queried_lots:
            break
        await asyncio.sleep(0.02)

    orchestrator.request_stop()
    await run_task

    assert url2 in queried_lots
    assert len(orchestrator.lots) == 2


def test_orchestrator_get_patrol_interval_for_lot_tiers(tmp_path: Path) -> None:
    """Verify cadence assignment based on remaining time tiers."""
    excel_path = tmp_path / "cartera_tiers.xlsx"
    url = "https://www.catawiki.com/es/l/900001-reloj"
    create_excel_template(excel_path, default_budget_eur=1000, sample_rows=[(url, 200)])
    orchestrator = AuctionOrchestrator(excel_path)
    lot = orchestrator.lots[0]

    # Without known remaining time (PENDING / newly created)
    lot.seconds_remaining = None
    assert orchestrator.get_patrol_interval_for_lot(lot) == 0.0

    # Far Tier (> 3600s / 1h) -> 300s
    lot.seconds_remaining = 3601.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 300.0
    lot.seconds_remaining = 86400.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 300.0

    # Medium Tier (900s - 3600s) -> 120s
    lot.seconds_remaining = 3600.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 120.0
    lot.seconds_remaining = 1800.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 120.0
    lot.seconds_remaining = 901.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 120.0

    # Near Tier (300s - 900s) -> 60s
    lot.seconds_remaining = 900.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 60.0
    lot.seconds_remaining = 500.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 60.0
    lot.seconds_remaining = 301.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 60.0

    # At assault threshold or lower (<= 300s) -> 0.0
    lot.seconds_remaining = 300.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 0.0
    lot.seconds_remaining = 25.0
    assert orchestrator.get_patrol_interval_for_lot(lot) == 0.0


@pytest.mark.asyncio
async def test_orchestrator_patrol_skips_lots_whose_interval_has_not_elapsed(tmp_path: Path) -> None:
    """Verify that run_patrol_step only queries lots whose assigned interval has elapsed."""
    excel_path = tmp_path / "cartera_skip_patrol.xlsx"
    url_far = "https://www.catawiki.com/es/l/900010-far"
    url_med = "https://www.catawiki.com/es/l/900020-med"
    url_near = "https://www.catawiki.com/es/l/900030-near"
    create_excel_template(
        excel_path,
        default_budget_eur=1500,
        sample_rows=[(url_far, 200), (url_med, 200), (url_near, 200)],
    )

    query_counts: dict[str, int] = {url_far: 0, url_med: 0, url_near: 0}

    class MockPatrolController:
        def __init__(self, target_url: str) -> None:
            self.target_url = target_url

        async def extract_snapshot(self) -> LotPageSnapshot:
            query_counts[self.target_url] += 1
            if "far" in self.target_url:
                sec = 7200.0  # > 3600s -> intervalo 300s
            elif "med" in self.target_url:
                sec = 1800.0  # 900-3600s -> intervalo 120s
            else:
                sec = 600.0   # 300-900s -> intervalo 60s
            return LotPageSnapshot(
                title="Mock Watch",
                current_bid_eur=50,
                next_min_bid_eur=55,
                seconds_remaining=sec,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            raise NotImplementedError

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> MockPatrolController:
        return MockPatrolController(lot.url)

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
    )

    # 1. First pass: since all 3 lots have no _last_patrolled_at, all are queried
    await orchestrator.run_patrol_step()
    assert query_counts[url_far] == 1
    assert query_counts[url_med] == 1
    assert query_counts[url_near] == 1

    # 2. Immediate second pass: no interval has elapsed -> none should be queried
    await orchestrator.run_patrol_step()
    assert query_counts[url_far] == 1
    assert query_counts[url_med] == 1
    assert query_counts[url_near] == 1

    # 3. Simulate 65 seconds elapsed only for url_near (interval = 60s)
    lot_near = orchestrator.lots[2]
    orchestrator._last_patrolled_at[lot_near.lot_id] -= 65.0

    await orchestrator.run_patrol_step()
    # Only url_near should have been queried again
    assert query_counts[url_far] == 1
    assert query_counts[url_med] == 1
    assert query_counts[url_near] == 2

    # 4. Simulate 125 seconds elapsed for url_med (interval = 120s)
    lot_med = orchestrator.lots[1]
    orchestrator._last_patrolled_at[lot_med.lot_id] -= 125.0

    await orchestrator.run_patrol_step()
    assert query_counts[url_far] == 1
    assert query_counts[url_med] == 2
    assert query_counts[url_near] == 2  # url_near has not accumulated another 60s yet


def test_orchestrator_calculate_patrol_wait_seconds_tiered_and_assault_anticipation(tmp_path: Path) -> None:
    """Verify wait calculation with tiered intervals and anticipation of the 300s threshold."""
    excel_path = tmp_path / "cartera_wait_tiered.xlsx"
    url_far = "https://www.catawiki.com/es/l/900010-far"
    url_med = "https://www.catawiki.com/es/l/900020-med"
    url_near = "https://www.catawiki.com/es/l/900030-near"
    create_excel_template(
        excel_path,
        default_budget_eur=1500,
        sample_rows=[(url_far, 200), (url_med, 200), (url_near, 200)],
    )

    orchestrator = AuctionOrchestrator(excel_path)
    lot_far, lot_med, lot_near = orchestrator.lots

    # Case 1: New lot without known seconds_remaining -> wait 0.5s for immediate query
    assert orchestrator._calculate_patrol_wait_seconds() == 0.5

    # Case 2: Only far lots (>3600s) -> wait 300s
    lot_far.seconds_remaining = 7200.0
    wait_far_only = orchestrator._calculate_patrol_wait_seconds(pending_lots=[lot_far])
    assert wait_far_only == 300.0

    # Case 3: Medium lot (1800s) and far lot -> minimum is 120s
    lot_med.seconds_remaining = 1800.0
    wait_med_far = orchestrator._calculate_patrol_wait_seconds(pending_lots=[lot_far, lot_med])
    assert wait_med_far == 120.0

    # Case 4: Near lot (600s) -> minimum is 60s
    lot_near.seconds_remaining = 600.0
    wait_all = orchestrator._calculate_patrol_wait_seconds(pending_lots=[lot_far, lot_med, lot_near])
    assert wait_all == 60.0

    # Case 5: Adaptive anticipation to Assault: lot at 306s (6s left to 300s < 60s)
    lot_near.seconds_remaining = 306.0
    wait_adaptive = orchestrator._calculate_patrol_wait_seconds(pending_lots=[lot_far, lot_med, lot_near])
    assert wait_adaptive == 6.0


@pytest.mark.asyncio
async def test_orchestrator_mid_patrol_excel_live_reload_inspects_new_lot_immediately(
    tmp_path: Path,
) -> None:
    """
    Verify that if a user adds a new watch row to Excel while run_patrol_step()
    is actively inspecting existing lots, the orchestrator detects the change
    between lots and patrols the newly added watch in the same patrol pass.
    """
    excel_path = tmp_path / "cartera_mid_patrol.xlsx"
    url1 = "https://www.catawiki.com/es/l/960001-seiko-one"
    url2 = "https://www.catawiki.com/es/l/960002-seiko-two"
    url3 = "https://www.catawiki.com/es/l/960003-seiko-three-added-mid-patrol"
    create_excel_template(
        excel_path,
        default_budget_eur=2000,
        sample_rows=[(url1, 400), (url2, 400)],
    )

    inspected_urls: list[str] = []

    class MidPatrolController:
        def __init__(self, target_url: str) -> None:
            self.target_url = target_url

        async def extract_snapshot(self) -> LotPageSnapshot:
            inspected_urls.append(self.target_url)
            if self.target_url == url1:
                # Simulate user saving a new row (row 9) in Excel while lot 1 is being inspected
                wb = load_workbook(excel_path)
                ws = wb.active
                assert ws is not None
                ws["A9"].value = url3
                ws["B9"].value = 350
                wb.save(excel_path)
                wb.close()
            return LotPageSnapshot(
                title=f"Title for {self.target_url}",
                current_bid_eur=100,
                next_min_bid_eur=110,
                seconds_remaining=7200.0,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            raise NotImplementedError

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> MidPatrolController:
        return MidPatrolController(lot.url)

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
    )

    await orchestrator.run_patrol_step()

    assert inspected_urls == [url1, url2, url3]
    assert len(orchestrator.lots) == 3
    assert orchestrator.lots[2].url == url3
    assert orchestrator.lots[2].status == AuctionStatus.PATROL


@pytest.mark.asyncio
async def test_orchestrator_duplicate_lot_id_rows_patrolled_independently(
    tmp_path: Path,
) -> None:
    """
    Verify that two Excel rows pointing to the same Catawiki lot ID (e.g. with
    different search query parameters or identical URLs) receive distinct lot_ids,
    preserve 1-to-1 signature parity, and do not starve each other in patrol rounds.
    """
    excel_path = tmp_path / "cartera_dup_lot_id.xlsx"
    url_a = "https://www.catawiki.com/es/l/107625784-seiko-chrono?po=search&poq=reloj+seiko"
    url_b = "https://www.catawiki.com/es/l/107625784-seiko-chrono?po=search&poq=seiko"
    create_excel_template(
        excel_path,
        default_budget_eur=1500,
        sample_rows=[(url_a, 450), (url_b, 290)],
    )

    inspected_rows: list[int | None] = []

    class DupLotController:
        def __init__(self, lot: WatchLot) -> None:
            self.lot = lot

        async def extract_snapshot(self) -> LotPageSnapshot:
            inspected_rows.append(self.lot.row_index)
            return LotPageSnapshot(
                title="Seiko Chronograph",
                current_bid_eur=200,
                next_min_bid_eur=220,
                seconds_remaining=500.0,
                is_leader=False,
                reserve_status=ReserveStatus.NO_RESERVE,
                is_closed=False,
            )

        async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
            raise NotImplementedError

        async def close(self) -> None:
            pass

    async def controller_factory(lot: WatchLot) -> DupLotController:
        return DupLotController(lot)

    orchestrator = AuctionOrchestrator(
        excel_path,
        controller_factory=controller_factory,
    )
    assert len(orchestrator.lots) == 2
    assert orchestrator.lots[0].lot_id != orchestrator.lots[1].lot_id

    # Round 1: both rows are patrolled
    await orchestrator.run_patrol_step()
    assert inspected_rows == [7, 8]

    # Simulate 65 seconds elapsed for both lots (near tier = 60s)
    for lot in orchestrator.lots:
        orchestrator._last_patrolled_at[lot.lot_id] -= 65.0

    # Round 2: both rows must be patrolled again (second row is not starved by first row)
    await orchestrator.run_patrol_step()
    assert inspected_rows == [7, 8, 7, 8]
