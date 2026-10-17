"""Orquestador principal (Modo Patrulla y Modo Asalto concurrente) para ClockAgent."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from clock_agent.browser.catawiki_page import CatawikiLotPage, LotPageSnapshot
from clock_agent.browser.cdp_client import CDPBrowserClient
from clock_agent.excel.sync_worker import ExcelSyncWorker
from clock_agent.excel.workbook_io import read_workbook_config
from clock_agent.models.auction import AuctionStatus, WatchLot
from clock_agent.strategy.ledger import BudgetLedger
from clock_agent.strategy.optimizer import PortfolioOptimizer
from clock_agent.strategy.tactics import TacticalBiddingEngine

logger = logging.getLogger(__name__)


class LotPageControllerProtocol(Protocol):
    """Protocolo para el controlador de una pestaña de lote (real vía CDP o simulada)."""

    async def extract_snapshot(self) -> LotPageSnapshot: ...

    async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot: ...

    async def close(self) -> None: ...


class PlaywrightLotController:
    """Adaptador que envuelve una pestaña Playwright real y su CatawikiLotPage."""

    def __init__(self, catawiki_page: CatawikiLotPage) -> None:
        self._cw_page = catawiki_page

    async def extract_snapshot(self) -> LotPageSnapshot:
        return await self._cw_page.extract_snapshot()

    async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
        return await self._cw_page.submit_minimum_bid(bid_amount_eur)

    async def close(self) -> None:
        try:
            await self._cw_page.page.close()
        except Exception:
            pass


ControllerFactory = Callable[[WatchLot], Awaitable[LotPageControllerProtocol]]


class AuctionOrchestrator:
    """
    Coordina el ciclo completo de vigilancia y pujas sobre múltiples lotes de Catawiki:
      - Modo Patrulla (> 5 min): lectura periódica secuencial y poda temprana.
      - Modo Asalto (<= 5 min y prórrogas +90s): tarea concurrente con pestaña dedicada
        por lote, contabilidad atómica (BudgetLedger), techo dinámico (PortfolioOptimizer),
        escalera de reserva y persistencia write-behind en Excel (ExcelSyncWorker).
    """

    def __init__(
        self,
        excel_path: str | Path,
        *,
        cdp_client: CDPBrowserClient | None = None,
        controller_factory: ControllerFactory | None = None,
        assault_threshold_seconds: float = 300.0,
        patrol_interval_seconds: float = 15.0,
        assault_tick_seconds: float = 0.25,
        snipe_window_seconds: float = 25.0,
        excel_retry_seconds: float = 2.0,
        on_state_change: Callable[[list[WatchLot], BudgetLedger], None] | None = None,
    ) -> None:
        self.excel_path = Path(excel_path)
        self.cdp_client = cdp_client
        self._controller_factory = controller_factory or self._default_controller_factory
        self.assault_threshold_seconds = assault_threshold_seconds
        self.patrol_interval_seconds = patrol_interval_seconds
        self.assault_tick_seconds = assault_tick_seconds
        self.on_state_change = on_state_change

        config = read_workbook_config(self.excel_path)
        self.ledger = BudgetLedger(config.global_budget_eur)
        self.lots: list[WatchLot] = config.lots
        self.optimizer = PortfolioOptimizer()
        self.tactics = TacticalBiddingEngine(snipe_window_seconds=snipe_window_seconds)
        self.sync_worker = ExcelSyncWorker(
            self.excel_path,
            retry_interval_seconds=excel_retry_seconds,
        )

        self._assault_tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()

    async def _default_controller_factory(self, lot: WatchLot) -> LotPageControllerProtocol:
        if self.cdp_client is None:
            raise RuntimeError("Se requiere un CDPBrowserClient o un controller_factory.")
        page = await self.cdp_client.new_page()
        cw_page = CatawikiLotPage(page)
        await cw_page.navigate(lot.url)
        return PlaywrightLotController(cw_page)

    def _emit_and_enqueue(self) -> None:
        """Recalcula techos dinámicos, notifica al callback de UI y encola persistencia Excel."""
        self.optimizer.refresh_all_ceilings(self.lots, self.ledger)
        self.sync_worker.enqueue_state(self.ledger.snapshot(), self.lots)
        if self.on_state_change is not None:
            self.on_state_change(self.lots, self.ledger)

    def has_active_lots(self) -> bool:
        """Devuelve True mientras quede al menos un lote no terminal."""
        return any(lot.status.is_active and not lot.is_closed for lot in self.lots)

    async def run_patrol_step(self) -> None:
        """
        Ejecuta una pasada de Modo Patrulla sobre los lotes activos que aún no están
        en Modo Asalto dedicado, y lanza tareas de Asalto para los que entren en <= 5 min.
        """
        for lot in self.lots:
            if not lot.status.is_active or lot.is_closed:
                continue
            if lot.lot_id in self._assault_tasks and not self._assault_tasks[lot.lot_id].done():
                continue

            controller = await self._controller_factory(lot)
            try:
                snap = await controller.extract_snapshot()
                lot.apply_market_snapshot(
                    current_bid_eur=snap.current_bid_eur,
                    next_min_bid_eur=snap.next_min_bid_eur,
                    seconds_remaining=snap.seconds_remaining,
                    is_leader=snap.is_leader,
                    reserve_status=snap.reserve_status,
                    is_closed=snap.is_closed,
                    title=snap.title or lot.title,
                    closing_time=snap.closing_time or lot.closing_time,
                )
            finally:
                await controller.close()

            # Si el lote ha cerrado durante la patrulla, consolidar en Ledger
            if lot.is_closed or lot.status.is_terminal:
                await self.tactics.process_tick_and_decide(
                    lot, self.lots, self.ledger, self.optimizer
                )
                self._emit_and_enqueue()
                continue

            self._emit_and_enqueue()

            # Conmutar a Modo Asalto si entra en los últimos 5 minutos
            if (
                lot.is_viable
                and lot.seconds_remaining is not None
                and lot.seconds_remaining <= self.assault_threshold_seconds
            ):
                self._spawn_assault_task(lot)

    def _spawn_assault_task(self, lot: WatchLot) -> None:
        existing = self._assault_tasks.get(lot.lot_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._run_assault_for_lot(lot),
            name=f"assault-{lot.lot_id}",
        )
        self._assault_tasks[lot.lot_id] = task

    async def _run_assault_for_lot(self, lot: WatchLot) -> None:
        """
        Mantiene una pestaña dedicada en Modo Asalto (< 5 min y prórrogas de +90s)
        hasta que el lote finalice o quede descartado.
        """
        controller = await self._controller_factory(lot)
        try:
            while not self._stop_event.is_set() and lot.status.is_active and not lot.is_closed:
                snap = await controller.extract_snapshot()
                lot.apply_market_snapshot(
                    current_bid_eur=snap.current_bid_eur,
                    next_min_bid_eur=snap.next_min_bid_eur,
                    seconds_remaining=snap.seconds_remaining,
                    is_leader=snap.is_leader,
                    reserve_status=snap.reserve_status,
                    is_closed=snap.is_closed,
                    title=snap.title or lot.title,
                    closing_time=snap.closing_time or lot.closing_time,
                )

                decision = await self.tactics.process_tick_and_decide(
                    lot, self.lots, self.ledger, self.optimizer
                )

                if decision.should_bid and decision.bid_amount_eur is not None:
                    bid_amt = decision.bid_amount_eur
                    try:
                        post_snap = await controller.submit_minimum_bid(bid_amt)
                        lot.record_own_bid(bid_amt, reserve_status=post_snap.reserve_status)
                        lot.apply_market_snapshot(
                            current_bid_eur=post_snap.current_bid_eur,
                            next_min_bid_eur=post_snap.next_min_bid_eur,
                            seconds_remaining=post_snap.seconds_remaining,
                            is_leader=post_snap.is_leader,
                            reserve_status=post_snap.reserve_status,
                            is_closed=post_snap.is_closed,
                            title=post_snap.title or lot.title,
                            closing_time=post_snap.closing_time or lot.closing_time,
                        )
                        # Si tras pujar otro postor nos superó en el mismo milisegundo, liberar reserva
                        if not lot.is_leader:
                            await self.ledger.release(lot.lot_id)
                    except Exception as exc:
                        logger.error("Error al enviar puja en %s: %s", lot.lot_id, exc)
                        await self.ledger.release(lot.lot_id)
                        lot.notes = f"Error de envío de puja: {exc}"

                self._emit_and_enqueue()

                if lot.is_closed or lot.status.is_terminal:
                    break

                await asyncio.sleep(self.assault_tick_seconds)

            # Consolidación final al salir del bucle de asalto
            await self.tactics.process_tick_and_decide(
                lot, self.lots, self.ledger, self.optimizer
            )
            self._emit_and_enqueue()
        finally:
            await controller.close()

    async def run_until_complete(self) -> None:
        """
        Ejecuta el orquestador completo (ExcelSyncWorker + bucle Patrulla/Asalto)
        hasta que todas las subastas hayan finalizado o se solicite parada.
        """
        if self.cdp_client is not None:
            if not self.cdp_client.is_connected:
                await self.cdp_client.connect()
            await self.cdp_client.verify_authenticated_session(require_auth=True)

        await self.sync_worker.start()
        self._emit_and_enqueue()

        try:
            while not self._stop_event.is_set() and (
                self.has_active_lots()
                or any(not t.done() for t in self._assault_tasks.values())
            ):
                await self.run_patrol_step()

                # Si todos los lotes activos ya están en tareas de asalto, esperar un tick breve
                if any(not t.done() for t in self._assault_tasks.values()):
                    await asyncio.sleep(min(self.patrol_interval_seconds, 0.05))
                elif self.has_active_lots():
                    await asyncio.sleep(self.patrol_interval_seconds)

            if self._assault_tasks:
                await asyncio.gather(*self._assault_tasks.values(), return_exceptions=True)
        finally:
            self._emit_and_enqueue()
            await self.sync_worker.stop(flush_remaining=True)

    def request_stop(self) -> None:
        """Solicita la parada ordenada del orquestador."""
        self._stop_event.set()
