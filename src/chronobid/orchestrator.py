"""Orquestador principal (Modo Patrulla y Modo Asalto concurrente) para ChronoBid."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from chronobid.browser.catawiki_page import (
    CatawikiLotPage,
    CatawikiPageLoadError,
    LotPageSnapshot,
)
from chronobid.browser.cdp_client import CDPBrowserClient
from chronobid.excel.sync_worker import ExcelSyncWorker
from chronobid.excel.workbook_io import read_workbook_config
from chronobid.models.auction import AuctionStatus, WatchLot
from chronobid.strategy.ledger import BudgetLedger
from chronobid.strategy.optimizer import PortfolioOptimizer
from chronobid.strategy.tactics import TacticalBiddingEngine

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

    def _get_pending_patrol_lots(self) -> list[WatchLot]:
        """Devuelve los lotes activos que aún requieren monitorización en Modo Patrulla."""
        return [
            lot
            for lot in self.lots
            if lot.status.is_active
            and not lot.is_closed
            and not (lot.lot_id in self._assault_tasks and not self._assault_tasks[lot.lot_id].done())
        ]

    def _calculate_patrol_wait_seconds(self, pending_lots: list[WatchLot] | None = None) -> float:
        """
        Calcula el tiempo de espera hasta la siguiente ronda de patrulla.
        Si algún lote viable está a punto de entrar en Modo Asalto (< patrol_interval_seconds),
        ajusta dinámicamente el tiempo para conmutar con precisión al umbral de 300 segundos.
        """
        wait_seconds = self.patrol_interval_seconds
        lots_to_check = pending_lots if pending_lots is not None else self._get_pending_patrol_lots()
        for lot in lots_to_check:
            if (
                lot.is_viable
                and lot.seconds_remaining is not None
                and lot.seconds_remaining > self.assault_threshold_seconds
            ):
                time_to_assault = lot.seconds_remaining - self.assault_threshold_seconds
                if 0 < time_to_assault < wait_seconds:
                    wait_seconds = max(0.5, time_to_assault)
        return wait_seconds

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

            try:
                controller = await self._controller_factory(lot)
            except Exception as exc:
                logger.warning("Fallo al conectar controlador para lote %s: %s", lot.lot_id, exc)
                lot.notes = f"Error puntual de conexión en Catawiki: {exc}"
                self._emit_and_enqueue()
                continue

            try:
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
                except CatawikiPageLoadError as err:
                    logger.warning("Error puntual de carga en Catawiki para %s: %s", lot.lot_id, err)
                    lot.notes = f"Error puntual de carga en Catawiki (reintentando en patrulla): {err}"
                    self._emit_and_enqueue()
                    continue
                except Exception as exc:
                    logger.error("Error inesperado al consultar lote %s: %s", lot.lot_id, exc)
                    lot.notes = f"Error de consulta en Catawiki: {exc}"
                    self._emit_and_enqueue()
                    continue
            finally:
                await controller.close()

            # Reconciliar estado del lote (cierre, liderazgo o superación) en BudgetLedger
            await self.tactics.reconcile_lot_with_ledger(lot, self.ledger)

            if lot.is_closed or lot.status.is_terminal:
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
                try:
                    snap = await controller.extract_snapshot()
                except CatawikiPageLoadError as err:
                    logger.warning("Fallo transitorio en asalto para %s: %s", lot.lot_id, err)
                    lot.notes = f"Fallo transitorio en asalto: {err}"
                    self._emit_and_enqueue()
                    await asyncio.sleep(self.assault_tick_seconds)
                    continue
                except Exception as exc:
                    logger.error("Error inesperado en asalto para %s: %s", lot.lot_id, exc)
                    lot.notes = f"Error en asalto: {exc}"
                    self._emit_and_enqueue()
                    await asyncio.sleep(self.assault_tick_seconds)
                    continue

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
                        bid_accepted = (
                            post_snap.is_leader or post_snap.current_bid_eur >= bid_amt
                        )
                        if bid_accepted:
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
                        if not bid_accepted:
                            logger.warning(
                                "Puja de %d € en %s no confirmada en Catawiki; liberando reserva.",
                                bid_amt,
                                lot.lot_id,
                            )
                            await self.ledger.release(lot.lot_id)
                            lot.notes = f"Puja de {bid_amt} € no confirmada en Catawiki"
                        elif not lot.is_leader:
                            # Si tras pujar otro postor nos superó en el mismo milisegundo, liberar reserva
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
                pending_patrol = self._get_pending_patrol_lots()
                if pending_patrol:
                    await self.run_patrol_step()

                active_assault_tasks = [
                    t for t in self._assault_tasks.values() if not t.done()
                ]
                pending_after_patrol = self._get_pending_patrol_lots()

                if pending_after_patrol:
                    # Quedan lotes en patrulla: esperar el intervalo de patrulla (con anticipación si procede)
                    wait_time = self._calculate_patrol_wait_seconds(pending_after_patrol)
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=wait_time)
                    except asyncio.TimeoutError:
                        pass
                elif active_assault_tasks:
                    # Todos los lotes activos están en tareas de asalto: esperar pasivamente a que alguna termine
                    # o que se solicite la parada, sin busy-wait de 50ms
                    try:
                        await asyncio.wait(
                            active_assault_tasks,
                            timeout=min(self.patrol_interval_seconds, 0.5),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except asyncio.TimeoutError:
                        pass
                elif self.has_active_lots():
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.patrol_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        pass

            if self._assault_tasks:
                await asyncio.gather(*self._assault_tasks.values(), return_exceptions=True)
        finally:
            self._emit_and_enqueue()
            await self.sync_worker.stop(flush_remaining=True)

    def request_stop(self) -> None:
        """Solicita la parada ordenada del orquestador."""
        self._stop_event.set()
