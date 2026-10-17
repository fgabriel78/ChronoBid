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
from chronobid.excel.workbook_io import (
    WorkbookConfigSnapshot,
    extract_input_signature,
    make_input_signature_from_memory,
    read_workbook_config,
)
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
        excel_poll_interval_seconds: float = 2.0,
        on_state_change: Callable[[list[WatchLot], BudgetLedger], None] | None = None,
    ) -> None:
        self.excel_path = Path(excel_path)
        self.cdp_client = cdp_client
        self._controller_factory = controller_factory or self._default_controller_factory
        self.assault_threshold_seconds = assault_threshold_seconds
        self.patrol_interval_seconds = patrol_interval_seconds
        self.assault_tick_seconds = assault_tick_seconds
        self.excel_poll_interval_seconds = excel_poll_interval_seconds
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
        self._reload_event = asyncio.Event()
        self._last_excel_mtime = self._get_excel_mtime()
        self._last_input_signature = make_input_signature_from_memory(
            config.global_budget_eur, self.lots
        )

    def _get_excel_mtime(self) -> float:
        """Obtiene la fecha de última modificación (mtime) del fichero Excel en disco."""
        try:
            return self.excel_path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return 0.0

    async def _check_and_reload_excel(self) -> bool:
        """
        Comprueba si el fichero Excel ha sido modificado en disco.
        Si la firma de entradas del usuario ha cambiado respecto a la activa en memoria,
        dispara la reconciliación del estado.
        Retorna True si hubo cambios reconciliados, False en caso contrario.
        """
        current_mtime = self._get_excel_mtime()
        if current_mtime == 0.0 or current_mtime <= self._last_excel_mtime:
            return False

        # El mtime cambió. Extraemos la firma de inputs (B1 y columnas A:B) para discriminar
        # entre una edición manual del usuario y un flush interno de ExcelSyncWorker.
        new_sig = extract_input_signature(self.excel_path)
        if new_sig is None:
            # Archivo temporalmente bloqueado o ilegible; no actualizar mtime para reintentar
            return False

        if new_sig == self._last_input_signature:
            # Fue una escritura interna de ChronoBid (o sin cambios de input)
            self._last_excel_mtime = current_mtime
            return False

        # Hay cambios de usuario en las entradas. Leemos la configuración completa.
        try:
            new_config = read_workbook_config(self.excel_path)
        except (PermissionError, OSError) as err:
            logger.debug("Bloqueo transitorio al leer configuración de Excel: %s", err)
            return False
        except Exception as exc:
            logger.warning("Error al recargar configuración de Excel: %s", exc)
            return False

        self._last_excel_mtime = current_mtime
        changed = await self.reconcile_with_excel(new_config)
        return changed

    async def reconcile_with_excel(self, new_config: WorkbookConfigSnapshot) -> bool:
        """
        Reconcilia en caliente el estado en memoria con la nueva configuración de Excel:
          - Sincroniza el presupuesto global en el ledger.
          - Actualiza topes de puja y filas de lotes existentes.
          - Reactiva lotes descartados por exceso de tope si el nuevo tope los hace viables.
          - Da de alta nuevos lotes en estado PENDING y despierta la patrulla.
          - Da de baja lotes eliminados del Excel (cancela tareas de asalto y libera reservas).
        Retorna True si hubo cambios aplicados.
        """
        changed = False

        # 1. Presupuesto global (B1)
        if new_config.global_budget_eur != self.ledger.global_budget_eur:
            try:
                await self.ledger.update_global_budget(new_config.global_budget_eur)
                logger.info(
                    "Presupuesto global actualizado en caliente: %d € -> %d €",
                    self.ledger.global_budget_eur,
                    new_config.global_budget_eur,
                )
                changed = True
            except ValueError as exc:
                logger.warning(
                    "No se pudo actualizar el presupuesto global a %d €: %s",
                    new_config.global_budget_eur,
                    exc,
                )

        # Mapeo de lotes por URL
        existing_by_url = {lot.url: lot for lot in self.lots}
        new_by_url = {lot.url: lot for lot in new_config.lots}

        # 2. Bajas: lotes existentes cuya URL ya no está en new_config
        lots_to_remove = [lot for url, lot in existing_by_url.items() if url not in new_by_url]
        for lot in lots_to_remove:
            if lot.lot_id in self._assault_tasks:
                task = self._assault_tasks.pop(lot.lot_id)
                task.cancel()
                logger.info("Tarea de asalto cancelada para lote eliminado: %s", lot.lot_id)
            if lot.status != AuctionStatus.WON:
                await self.ledger.release(lot.lot_id)
            changed = True
            logger.info("Lote eliminado del seguimiento: %s (%s)", lot.lot_id, lot.url)

        remaining_lots = [lot for lot in self.lots if lot.url in new_by_url]

        # 3. Modificaciones de lotes existentes
        for lot in remaining_lots:
            cfg = new_by_url[lot.url]
            if lot.row_index != cfg.row_index:
                lot.row_index = cfg.row_index
                changed = True

            if lot.status == AuctionStatus.VALIDATION_ERROR and cfg.validate_inputs():
                lot.max_bid_eur = cfg.max_bid_eur
                lot.status = AuctionStatus.PENDING
                lot.notes = ""
                changed = True
                self._reload_event.set()
                logger.info("Lote %s corregido y reactivado a PENDING", lot.lot_id)
            elif lot.max_bid_eur != cfg.max_bid_eur:
                old_max = lot.max_bid_eur
                lot.max_bid_eur = cfg.max_bid_eur
                changed = True
                logger.info(
                    "Tope de puja actualizado para %s: %d € -> %d €",
                    lot.lot_id,
                    old_max,
                    lot.max_bid_eur,
                )

                if lot.status == AuctionStatus.DISCARDED_MAX_EXCEEDED and not lot.is_closed:
                    if lot.next_min_bid_eur is not None and lot.max_bid_eur >= lot.next_min_bid_eur:
                        if (
                            lot.seconds_remaining is not None
                            and lot.seconds_remaining <= self.assault_threshold_seconds
                        ):
                            lot.status = AuctionStatus.ASSAULT
                            self._spawn_assault_task(lot)
                        else:
                            lot.status = AuctionStatus.PATROL
                        lot.notes = f"Reactivado tras aumento de puja máxima a {lot.max_bid_eur} €"
                        self._reload_event.set()
                        logger.info(
                            "Lote %s reactivado a %s tras aumento de tope",
                            lot.lot_id,
                            lot.status.value,
                        )
                elif (
                    not lot.is_leader
                    and lot.next_min_bid_eur is not None
                    and lot.next_min_bid_eur > 0
                    and lot.max_bid_eur < lot.next_min_bid_eur
                    and not lot.is_closed
                ):
                    lot.status = AuctionStatus.DISCARDED_MAX_EXCEEDED
                    lot.notes = (
                        f"Descartado: nuevo tope ({lot.max_bid_eur} €) menor que puja mínima "
                        f"({lot.next_min_bid_eur} €)"
                    )
                    logger.info(
                        "Lote %s descartado por reducción de tope por debajo de la puja mínima",
                        lot.lot_id,
                    )

        # 4. Altas: lotes en new_config cuya URL no estaba en existing_by_url
        new_lots = [lot for url, lot in new_by_url.items() if url not in existing_by_url]
        if new_lots:
            remaining_lots.extend(new_lots)
            changed = True
            self._reload_event.set()
            for nlot in new_lots:
                logger.info(
                    "Nuevo lote añadido al catálogo: %s (fila %s, max %d €)",
                    nlot.lot_id,
                    nlot.row_index,
                    nlot.max_bid_eur,
                )

        remaining_lots.sort(key=lambda x: (x.row_index if x.row_index is not None else 999999))
        self.lots = remaining_lots

        if changed:
            self._last_input_signature = make_input_signature_from_memory(
                self.ledger.global_budget_eur, self.lots
            )
            self._emit_and_enqueue()

        return changed

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

    async def _wait_interval_with_reload_check(self, total_wait_seconds: float) -> None:
        """
        Espera hasta `total_wait_seconds` en tramos de `excel_poll_interval_seconds`.
        En cada tramo verifica si el Excel cambió. Si se detecta un nuevo lote o reactivación
        (_reload_event.is_set()), o se solicita parada (_stop_event.is_set()), despierta de inmediato.
        """
        elapsed = 0.0
        while elapsed < total_wait_seconds and not self._stop_event.is_set():
            if self._reload_event.is_set():
                self._reload_event.clear()
                break
            chunk = min(self.excel_poll_interval_seconds, total_wait_seconds - elapsed)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=chunk)
                break
            except asyncio.TimeoutError:
                pass
            elapsed += chunk
            if self._stop_event.is_set():
                break
            await self._check_and_reload_excel()
            if self._reload_event.is_set():
                self._reload_event.clear()
                break

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
                await self._check_and_reload_excel()

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
                    await self._wait_interval_with_reload_check(wait_time)
                elif active_assault_tasks:
                    # Todos los lotes activos están en tareas de asalto: esperar pasivamente a que alguna termine
                    # o que venza un intervalo breve, comprobando recargas en caliente
                    try:
                        await asyncio.wait(
                            active_assault_tasks,
                            timeout=min(self.excel_poll_interval_seconds, 0.5),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except asyncio.TimeoutError:
                        pass
                    await self._check_and_reload_excel()
                    if self._reload_event.is_set():
                        self._reload_event.clear()
                elif self.has_active_lots():
                    await self._wait_interval_with_reload_check(self.patrol_interval_seconds)

            if self._assault_tasks:
                await asyncio.gather(*self._assault_tasks.values(), return_exceptions=True)
        finally:
            self._emit_and_enqueue()
            await self.sync_worker.stop(flush_remaining=True)

    def request_stop(self) -> None:
        """Solicita la parada ordenada del orquestador."""
        self._stop_event.set()
