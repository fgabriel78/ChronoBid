"""Main orchestrator (Patrol Mode and concurrent Assault Mode) for ChronoBid."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from chronobid.browser.catawiki_page import (
    CatawikiLotPage,
    CatawikiPageLoadError,
    HumanVerificationBlockedError,
    LotPageSnapshot,
)
from chronobid.browser.cdp_client import CDPBrowserClient
from chronobid.diagnostics import DecisionTracer
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
    """Protocol for a lot tab controller (real via CDP or simulated)."""

    async def extract_snapshot(self) -> LotPageSnapshot: ...

    async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot: ...

    async def ensure_no_challenge(self) -> bool: ...

    async def close(self) -> None: ...


class PlaywrightLotController:
    """Adapter wrapping a real Playwright tab and its CatawikiLotPage."""

    def __init__(self, catawiki_page: CatawikiLotPage) -> None:
        self._cw_page = catawiki_page

    async def extract_snapshot(self) -> LotPageSnapshot:
        return await self._cw_page.extract_snapshot()

    async def submit_minimum_bid(self, bid_amount_eur: int) -> LotPageSnapshot:
        return await self._cw_page.submit_minimum_bid(bid_amount_eur)

    async def ensure_no_challenge(self) -> bool:
        return await self._cw_page.ensure_no_challenge()

    async def close(self) -> None:
        try:
            await self._cw_page.page.close()
        except Exception:
            pass


ControllerFactory = Callable[[WatchLot], Awaitable[LotPageControllerProtocol]]


class AuctionOrchestrator:
    """
    Coordinates full monitoring and bidding lifecycle across multiple Catawiki lots:
      - Patrol Mode (> 5 min): sequential periodic reads and early pruning.
      - Assault Mode (<= 5 min and +90s extensions): concurrent task with dedicated tab
        per lot, atomic accounting (BudgetLedger), dynamic ceiling (PortfolioOptimizer),
        reserve ladder, and write-behind persistence to Excel (ExcelSyncWorker).
    """

    def __init__(
        self,
        excel_path: str | Path,
        *,
        cdp_client: CDPBrowserClient | None = None,
        controller_factory: ControllerFactory | None = None,
        assault_threshold_seconds: float = 300.0,
        patrol_interval_seconds: float = 60.0,
        patrol_interval_medium_seconds: float | None = None,
        patrol_interval_far_seconds: float | None = None,
        assault_tick_seconds: float = 0.25,
        snipe_window_seconds: float = 25.0,
        pre_snipe_check_seconds: float = 10.0,
        excel_retry_seconds: float = 2.0,
        excel_poll_interval_seconds: float = 2.0,
        on_state_change: Callable[[list[WatchLot], BudgetLedger], None] | None = None,
    ) -> None:
        self.excel_path = Path(excel_path)
        self.cdp_client = cdp_client
        self._controller_factory = controller_factory or self._default_controller_factory
        self.assault_threshold_seconds = assault_threshold_seconds
        self.patrol_interval_seconds = patrol_interval_seconds
        if patrol_interval_medium_seconds is not None:
            self.patrol_interval_medium_seconds = patrol_interval_medium_seconds
        else:
            self.patrol_interval_medium_seconds = (
                120.0 if patrol_interval_seconds == 60.0 else patrol_interval_seconds * 2.0
            )
        if patrol_interval_far_seconds is not None:
            self.patrol_interval_far_seconds = patrol_interval_far_seconds
        else:
            self.patrol_interval_far_seconds = (
                300.0 if patrol_interval_seconds == 60.0 else patrol_interval_seconds * 5.0
            )
        self.assault_tick_seconds = assault_tick_seconds
        self.pre_snipe_check_seconds = max(0.0, pre_snipe_check_seconds)
        self.excel_poll_interval_seconds = excel_poll_interval_seconds
        self.on_state_change = on_state_change

        config = read_workbook_config(self.excel_path)
        self.ledger = BudgetLedger(config.global_budget_eur)
        self.lots: list[WatchLot] = config.lots
        self.optimizer = PortfolioOptimizer()
        self.tactics = TacticalBiddingEngine(snipe_window_seconds=snipe_window_seconds)
        self.decision_tracer = DecisionTracer(
            snipe_window_seconds=snipe_window_seconds,
            overtime_window_seconds=self.tactics.overtime_window_seconds,
        )
        self._outcome_logged: set[int] = set()
        self.sync_worker = ExcelSyncWorker(
            self.excel_path,
            retry_interval_seconds=excel_retry_seconds,
        )

        self._assault_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_patrolled_at: dict[str, float] = {}
        self._stop_event = asyncio.Event()
        self._reload_event = asyncio.Event()
        self._last_excel_mtime = self._get_excel_mtime()
        self._last_input_signature = make_input_signature_from_memory(
            config.global_budget_eur, self.lots
        )

    def get_patrol_interval_for_lot(self, lot: WatchLot) -> float:
        """
        Determines the tiered query interval for a lot based on its remaining time:
          - > 3600s (> 1 hour): 300s (5 min).
          - 900s - 3600s (15 to 60 min): 120s (2 min).
          - 300s - 900s (5 to 15 min): 60s (1 min).
          - <= 300s or unknown remaining time (PENDING): 0.0s (immediate query / assault).
        """
        if lot.seconds_remaining is None:
            return 0.0
        if lot.seconds_remaining > 3600.0:
            return self.patrol_interval_far_seconds
        if lot.seconds_remaining > 900.0:
            return self.patrol_interval_medium_seconds
        if lot.seconds_remaining > self.assault_threshold_seconds:
            return self.patrol_interval_seconds
        return 0.0

    def _get_excel_mtime(self) -> float:
        """Gets last modification time (mtime) of the Excel file on disk."""
        try:
            return self.excel_path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return 0.0

    async def _check_and_reload_excel(self) -> bool:
        """
        Checks whether the Excel file was modified on disk.
        If user input signature changed compared to active in-memory signature,
        triggers state reconciliation.
        Returns True if changes were reconciled, False otherwise.
        """
        current_mtime = self._get_excel_mtime()
        if current_mtime == 0.0 or current_mtime <= self._last_excel_mtime:
            return False

        # mtime changed. Extract input signature (B1 and columns A:B) to discriminate
        # between manual user editing and internal flush from ExcelSyncWorker.
        new_sig = extract_input_signature(self.excel_path)
        if new_sig is None:
            # File temporarily locked or unreadable; do not update mtime so we retry
            return False

        if new_sig == self._last_input_signature:
            # Was an internal ChronoBid write (or no input changes)
            self._last_excel_mtime = current_mtime
            return False

        # User changed inputs. Read full configuration.
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
        Reconciles in-memory state live with new Excel configuration:
          - Synchronizes global budget in ledger.
          - Updates bid ceilings and rows for existing lots.
          - Reactivates lots discarded for exceeding ceiling if new ceiling makes them viable.
          - Adds new lots in PENDING state and wakes up patrol.
          - Removes lots deleted from Excel (cancels assault tasks and releases reservations).
        Returns True if changes were applied.
        """
        changed = False

        # 1. Global budget (B1)
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

        # Match existing lots to new_config.lots preserving 1-to-1 row parity (including duplicate URLs)
        unmatched_existing: list[WatchLot] = list(self.lots)
        matched_pairs: list[tuple[WatchLot, WatchLot]] = []
        unmatched_new_indices: list[int] = []

        # Pass 1: exact (url, row_index) match
        for idx, cfg in enumerate(new_config.lots):
            match_lot = next(
                (
                    ex
                    for ex in unmatched_existing
                    if ex.url == cfg.url and ex.row_index == cfg.row_index
                ),
                None,
            )
            if match_lot is not None:
                unmatched_existing.remove(match_lot)
                matched_pairs.append((match_lot, cfg))
            else:
                unmatched_new_indices.append(idx)

        # Pass 2: fallback match by URL for rows that shifted index
        new_lots: list[WatchLot] = []
        for idx in unmatched_new_indices:
            cfg = new_config.lots[idx]
            match_lot = next(
                (ex for ex in unmatched_existing if ex.url == cfg.url),
                None,
            )
            if match_lot is not None:
                unmatched_existing.remove(match_lot)
                matched_pairs.append((match_lot, cfg))
            else:
                new_lots.append(cfg)

        # 2. Removals: existing lots that did not match any row in new_config
        for lot in unmatched_existing:
            if lot.lot_id in self._assault_tasks:
                task = self._assault_tasks.pop(lot.lot_id)
                task.cancel()
                logger.info("Tarea de asalto cancelada para lote eliminado: %s", lot.lot_id)
            if lot.status != AuctionStatus.WON:
                await self.ledger.release(lot.lot_id)
            self._last_patrolled_at.pop(lot.lot_id, None)
            changed = True
            logger.info("Lote eliminado del seguimiento: %s (%s)", lot.lot_id, lot.url)

        remaining_lots: list[WatchLot] = [pair[0] for pair in matched_pairs]

        # 3. Modifications of existing lots
        for lot, cfg in matched_pairs:
            if lot.row_index != cfg.row_index:
                lot.row_index = cfg.row_index
                changed = True

            if lot.lot_id != cfg.lot_id:
                old_id = lot.lot_id
                lot.lot_id = cfg.lot_id
                if old_id in self._last_patrolled_at:
                    self._last_patrolled_at[lot.lot_id] = self._last_patrolled_at.pop(old_id)
                if old_id in self._assault_tasks:
                    self._assault_tasks[lot.lot_id] = self._assault_tasks.pop(old_id)
                changed = True

            if lot.status == AuctionStatus.VALIDATION_ERROR and cfg.validate_inputs():
                lot.max_bid_eur = cfg.max_bid_eur
                lot.status = AuctionStatus.PENDING
                lot.notes = ""
                self._last_patrolled_at.pop(lot.lot_id, None)
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
                            self._last_patrolled_at.pop(lot.lot_id, None)
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

        # 4. Additions: new lots not matched to any existing lot
        if new_lots:
            remaining_lots.extend(new_lots)
            changed = True
            self._reload_event.set()
            for nlot in new_lots:
                self._last_patrolled_at.pop(nlot.lot_id, None)
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
        """Recalculates dynamic ceilings, notifies UI callback, and enqueues Excel persistence."""
        self.optimizer.refresh_all_ceilings(self.lots, self.ledger)
        self._log_final_outcomes()
        self.sync_worker.enqueue_state(self.ledger.snapshot(), self.lots)
        if self.on_state_change is not None:
            self.on_state_change(self.lots, self.ledger)

    def _log_final_outcomes(self) -> None:
        """Logs the final outcome of each lot exactly once when it becomes terminal."""
        for lot in self.lots:
            if not lot.status.is_terminal or id(lot) in self._outcome_logged:
                continue
            self._outcome_logged.add(id(lot))
            logger.info(
                "FINAL_OUTCOME lot=%s row=%s status=%s closing_price=%d my_last_bid=%d "
                "max=%d last_event=%s",
                lot.lot_id,
                lot.row_index,
                lot.status.value,
                lot.current_bid_eur,
                lot.my_last_bid_eur,
                lot.max_bid_eur,
                lot.last_event or "-",
            )

    def has_active_lots(self) -> bool:
        """Returns True while at least one non-terminal lot remains."""
        return any(lot.status.is_active and not lot.is_closed for lot in self.lots)

    def _get_pending_patrol_lots(self) -> list[WatchLot]:
        """Returns active lots that still require Patrol Mode monitoring."""
        return [
            lot
            for lot in self.lots
            if lot.status.is_active
            and not lot.is_closed
            and not (lot.lot_id in self._assault_tasks and not self._assault_tasks[lot.lot_id].done())
        ]

    def _calculate_patrol_wait_seconds(self, pending_lots: list[WatchLot] | None = None) -> float:
        """
        Calculates wait time until next patrol round or assault transition.
        For each active lot:
          - Evaluates tiered interval according to remaining time.
          - Accurately synchronizes transition to Assault Mode if near entry (< assault_threshold_seconds).
          - Returns minimum required wait time among all pending lots.
        """
        lots_to_check = pending_lots if pending_lots is not None else self._get_pending_patrol_lots()
        if not lots_to_check:
            return self.patrol_interval_seconds

        now = time.monotonic()
        candidates: list[float] = []

        for lot in lots_to_check:
            interval = self.get_patrol_interval_for_lot(lot)
            last_polled = self._last_patrolled_at.get(lot.lot_id)

            if last_polled is None:
                if lot.seconds_remaining is not None and interval > 0:
                    time_to_poll = interval
                else:
                    time_to_poll = 0.5
            else:
                elapsed = now - last_polled
                time_to_poll = max(0.5, interval - elapsed)

            # Adaptive anticipation of Assault Mode (300s)
            if (
                lot.is_viable
                and lot.seconds_remaining is not None
                and lot.seconds_remaining > self.assault_threshold_seconds
            ):
                time_to_assault = lot.seconds_remaining - self.assault_threshold_seconds
                if 0 < time_to_assault < time_to_poll:
                    time_to_poll = max(0.5, time_to_assault)

            candidates.append(time_to_poll)

        return min(candidates) if candidates else self.patrol_interval_seconds

    async def run_patrol_step(self, *, force: bool = False) -> None:
        """
        Executes a Patrol Mode pass over active lots not yet in dedicated
        Assault Mode, and launches Assault tasks for lots entering <= 5 min.
        Filters and queries only lots whose assigned interval has elapsed or
        that have no registered prior query (unless force=True).
        Checks for live Excel updates between sequential lot queries.
        """
        visited_ids: set[int] = set()
        inspected_any = False

        while not self._stop_event.is_set():
            if inspected_any:
                if await self._check_and_reload_excel():
                    for item in self.lots:
                        if item.lot_id not in self._last_patrolled_at:
                            visited_ids.discard(id(item))
                if self._reload_event.is_set():
                    self._reload_event.clear()

            lot = next((item for item in self.lots if id(item) not in visited_ids), None)
            if lot is None:
                break
            visited_ids.add(id(lot))

            if not lot.status.is_active or lot.is_closed:
                continue
            if lot.lot_id in self._assault_tasks and not self._assault_tasks[lot.lot_id].done():
                continue

            now = time.monotonic()
            interval = self.get_patrol_interval_for_lot(lot)
            last_polled = self._last_patrolled_at.get(lot.lot_id)
            if not force and last_polled is not None and lot.seconds_remaining is not None:
                if (now - last_polled) < interval:
                    continue

            inspected_any = True
            try:
                controller = await self._controller_factory(lot)
            except Exception as exc:
                self._last_patrolled_at[lot.lot_id] = time.monotonic()
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
                except HumanVerificationBlockedError as err:
                    logger.warning("Verificación humana sin resolver en patrulla para %s: %s", lot.lot_id, err)
                    lot.mark_verification_blocked(str(err))
                    self._emit_and_enqueue()
                    if (
                        lot.seconds_remaining is not None
                        and lot.seconds_remaining <= self.assault_threshold_seconds
                    ):
                        # Assault Mode retries resolution on every tick instead of waiting for patrol
                        self._spawn_assault_task(lot)
                    continue
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
                self._last_patrolled_at[lot.lot_id] = time.monotonic()
                await controller.close()

            # Reconcile lot state (closing, leadership, or outbid) in BudgetLedger
            await self.tactics.reconcile_lot_with_ledger(lot, self.ledger)

            if lot.is_closed or lot.status.is_terminal:
                self._emit_and_enqueue()
                continue

            self._emit_and_enqueue()

            # Switch to Assault Mode if entering last 5 minutes
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

    async def _pre_snipe_challenge_check(
        self, controller: LotPageControllerProtocol, lot: WatchLot
    ) -> bool:
        """
        Verifies that no human verification challenge is pending before the snipe window.
        Returns False if the lot is blocked by an unresolved challenge, True otherwise.
        """
        check = getattr(controller, "ensure_no_challenge", None)
        if check is None:
            return True
        try:
            solved = await check()
            logger.info(
                "Comprobación pre-snipe en %s (%.1fs restantes): %s",
                lot.lot_id,
                lot.seconds_remaining or 0.0,
                "reto resuelto" if solved else "sin reto",
            )
            if solved:
                lot.record_event("Verificación humana resuelta antes de la ventana de puja")
            return True
        except HumanVerificationBlockedError as err:
            logger.warning("Comprobación pre-snipe bloqueada en %s: %s", lot.lot_id, err)
            lot.mark_verification_blocked(str(err))
            return False
        except Exception as exc:
            logger.error("Error en comprobación pre-snipe de %s: %s", lot.lot_id, exc)
            return True

    async def _run_assault_for_lot(self, lot: WatchLot) -> None:
        """
        Maintains a dedicated tab in Assault Mode (< 5 min and +90s extensions)
        until the lot finishes or is discarded.
        """
        logger.info(
            "ASSAULT_ENTRY lot=%s row=%s secs=%s current=%d max=%d",
            lot.lot_id,
            lot.row_index,
            lot.seconds_remaining,
            lot.current_bid_eur,
            lot.max_bid_eur,
        )
        controller = await self._controller_factory(lot)
        pre_snipe_threshold = self.tactics.snipe_window_seconds + self.pre_snipe_check_seconds
        pre_snipe_armed = True
        try:
            while not self._stop_event.is_set() and lot.status.is_active and not lot.is_closed:
                try:
                    snap = await controller.extract_snapshot()
                except HumanVerificationBlockedError as err:
                    logger.warning(
                        "INCIDENT lot=%s secs=%s type=verification_blocked: %s",
                        lot.lot_id,
                        lot.seconds_remaining,
                        err,
                    )
                    lot.mark_verification_blocked(str(err))
                    self._emit_and_enqueue()
                    await asyncio.sleep(self.assault_tick_seconds)
                    continue
                except CatawikiPageLoadError as err:
                    logger.warning(
                        "INCIDENT lot=%s secs=%s type=page_load_error: %s",
                        lot.lot_id,
                        lot.seconds_remaining,
                        err,
                    )
                    lot.record_event(f"Fallo transitorio en asalto: {err}")
                    self._emit_and_enqueue()
                    await asyncio.sleep(self.assault_tick_seconds)
                    continue
                except Exception as exc:
                    logger.error(
                        "INCIDENT lot=%s secs=%s type=unexpected_error: %s",
                        lot.lot_id,
                        lot.seconds_remaining,
                        exc,
                    )
                    lot.record_event(f"Error en asalto: {exc}")
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

                # Pre-snipe challenge clearance: once per crossing of the threshold,
                # re-armed when an overtime extension pushes time back above it.
                if lot.seconds_remaining is not None and not lot.is_closed:
                    if lot.seconds_remaining > pre_snipe_threshold:
                        pre_snipe_armed = True
                    elif pre_snipe_armed:
                        pre_snipe_armed = False
                        if not await self._pre_snipe_challenge_check(controller, lot):
                            self._emit_and_enqueue()
                            await asyncio.sleep(self.assault_tick_seconds)
                            continue

                decision = await self.tactics.process_tick_and_decide(
                    lot, self.lots, self.ledger, self.optimizer
                )
                self.decision_tracer.log_decision(lot, decision, self.ledger)

                if decision.should_bid and decision.bid_amount_eur is not None:
                    bid_amt = decision.bid_amount_eur
                    bid_started = time.monotonic()
                    secs_at_bid = lot.seconds_remaining
                    logger.info(
                        "BID_ATTEMPT lot=%s amount=%d secs=%s reason=%s",
                        lot.lot_id,
                        bid_amt,
                        f"{secs_at_bid:.1f}" if secs_at_bid is not None else "None",
                        decision.reason,
                    )
                    try:
                        post_snap = await controller.submit_minimum_bid(bid_amt)
                        bid_accepted = (
                            post_snap.is_leader or post_snap.current_bid_eur >= bid_amt
                        )
                        logger.info(
                            "BID_RESULT lot=%s amount=%d confirmed=%s duration=%.2fs "
                            "challenge_solved=%s post_current=%d post_leader=%s post_secs=%s",
                            lot.lot_id,
                            bid_amt,
                            bid_accepted,
                            time.monotonic() - bid_started,
                            bool(post_snap.extra.get("challenge_solved", False)),
                            post_snap.current_bid_eur,
                            post_snap.is_leader,
                            post_snap.seconds_remaining,
                        )
                        if bid_accepted:
                            lot.record_own_bid(bid_amt, reserve_status=post_snap.reserve_status)
                        else:
                            logger.warning(
                                "Puja de %d € en %s no confirmada en Catawiki; liberando reserva.",
                                bid_amt,
                                lot.lot_id,
                            )
                            await self.ledger.release(lot.lot_id)
                            lot.record_event(f"Puja de {bid_amt} € no confirmada en Catawiki")
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
                        if bid_accepted and not lot.is_leader:
                            # If outbid by another bidder in the same millisecond after bidding, release reservation
                            await self.ledger.release(lot.lot_id)
                    except HumanVerificationBlockedError as err:
                        logger.error(
                            "BID_RESULT lot=%s amount=%d confirmed=False duration=%.2fs "
                            "incident=verification_blocked: %s",
                            lot.lot_id,
                            bid_amt,
                            time.monotonic() - bid_started,
                            err,
                        )
                        await self.ledger.release(lot.lot_id)
                        lot.mark_verification_blocked(f"puja de {bid_amt} € no enviada ({err})")
                    except Exception as exc:
                        logger.error(
                            "BID_RESULT lot=%s amount=%d confirmed=False duration=%.2fs "
                            "incident=submission_error: %s",
                            lot.lot_id,
                            bid_amt,
                            time.monotonic() - bid_started,
                            exc,
                        )
                        await self.ledger.release(lot.lot_id)
                        lot.record_event(f"Error de envío de puja de {bid_amt} €: {exc}")

                self._emit_and_enqueue()

                if lot.is_closed or lot.status.is_terminal:
                    break

                await asyncio.sleep(self.assault_tick_seconds)

            # Final consolidation upon exiting assault loop
            await self.tactics.process_tick_and_decide(
                lot, self.lots, self.ledger, self.optimizer
            )
            self._emit_and_enqueue()
        finally:
            logger.info(
                "ASSAULT_EXIT lot=%s row=%s status=%s secs=%s",
                lot.lot_id,
                lot.row_index,
                lot.status.value,
                lot.seconds_remaining,
            )
            await controller.close()

    async def _wait_interval_with_reload_check(self, total_wait_seconds: float) -> None:
        """
        Waits up to `total_wait_seconds` in chunks of `excel_poll_interval_seconds`.
        In each chunk verifies whether Excel changed. If a new lot or reactivation
        is detected (_reload_event.is_set()), or stop is requested (_stop_event.is_set()),
        wakes up immediately.
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
        Runs the full orchestrator (ExcelSyncWorker + Patrol/Assault loop)
        until all auctions have finished or stop is requested.
        """
        logger.info(
            "SESSION_START excel=%s global_budget=%d lots=%d snipe=%.0fs assault=%.0fs",
            self.excel_path.resolve(),
            self.ledger.global_budget_eur,
            len(self.lots),
            self.tactics.snipe_window_seconds,
            self.assault_threshold_seconds,
        )
        for lot in self.lots:
            logger.info(
                "SESSION_LOT row=%s lot=%s max=%d status=%s url=%s",
                lot.row_index,
                lot.lot_id,
                lot.max_bid_eur,
                lot.status.value,
                lot.url,
            )

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
                    # Patrol lots remain: wait patrol interval (with anticipation if applicable)
                    wait_time = self._calculate_patrol_wait_seconds(pending_after_patrol)
                    await self._wait_interval_with_reload_check(wait_time)
                elif active_assault_tasks:
                    # All active lots are in assault tasks: passively wait for one to complete
                    # or brief interval to expire, checking live reloads
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
        """Requests orderly stop of the orchestrator."""
        self._stop_event.set()
