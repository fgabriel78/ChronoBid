"""Worker asíncrono write-behind para sincronizar el estado hacia Excel sin bloquear pujas."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from pathlib import Path

from chronobid.excel.workbook_io import (
    WorkbookConfigSnapshot,
    read_workbook_config,
    write_workbook_state,
)
from chronobid.models.auction import GlobalBudgetState, WatchLot

logger = logging.getLogger(__name__)

WriterFunc = Callable[[Path, GlobalBudgetState, list[WatchLot]], None]


class ExcelSyncWorker:
    """
    Gestiona la persistencia asíncrona en segundo plano (write-behind) hacia el fichero .xlsx.

    Si el archivo está bloqueado por Microsoft Excel en Windows (PermissionError),
    conserva el último estado en memoria sin bloquear el bucle de pujas y reintenta
    automáticamente hasta que el archivo quede libre.
    """

    def __init__(
        self,
        file_path: str | Path,
        *,
        retry_interval_seconds: float = 2.0,
        writer: WriterFunc = write_workbook_state,
    ) -> None:
        self.file_path = Path(file_path)
        self.retry_interval_seconds = retry_interval_seconds
        self._writer = writer

        self._latest_budget: GlobalBudgetState | None = None
        self._latest_lots: list[WatchLot] = []
        self._version: int = 0
        self._flushed_version: int = 0
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self.lock_Encountered_count: int = 0
        self.successful_writes_count: int = 0
        self.last_error: str | None = None

    @property
    def is_dirty(self) -> bool:
        """Indica si existen cambios en memoria pendientes de persistir en disco."""
        return self._version > self._flushed_version

    def enqueue_state(self, budget_state: GlobalBudgetState, lots: list[WatchLot]) -> None:
        """
        Registra en memoria una nueva instantánea de presupuesto y lotes de forma inmediata
        (no bloqueante) y despierta al worker de persistencia.
        """
        self._latest_budget = budget_state
        self._latest_lots = [copy.copy(lot) for lot in lots]
        self._version += 1
        self._wake_event.set()

    async def flush_once(self) -> bool:
        """
        Intenta volcar a disco la última instantánea pendiente.
        Devuelve True si el estado quedó sincronizado o no había cambios pendientes,
        o False si el archivo estaba bloqueado (PermissionError).
        """
        if not self.is_dirty or self._latest_budget is None:
            return True

        target_version = self._version
        budget_snapshot = self._latest_budget
        lots_snapshot = [copy.copy(lot) for lot in self._latest_lots]

        try:
            await asyncio.to_thread(
                self._writer,
                self.file_path,
                budget_snapshot,
                lots_snapshot,
            )
        except PermissionError as exc:
            self.lock_Encountered_count += 1
            self.last_error = f"Archivo Excel bloqueado temporalmente: {exc}"
            logger.warning(
                "Archivo Excel '%s' bloqueado por otro proceso; cambios encolados en memoria (intento #%d).",
                self.file_path.name,
                self.lock_Encountered_count,
            )
            return False
        except OSError as exc:
            # En Windows algunos bloqueos exclusivos de compartición se reportan como OSError (winerror 32/33)
            if getattr(exc, "winerror", None) in {32, 33} or isinstance(exc, PermissionError):
                self.lock_Encountered_count += 1
                self.last_error = f"Archivo Excel bloqueado por Windows: {exc}"
                logger.warning(
                    "Archivo Excel '%s' en uso (WinError); se reintentará automáticamente.",
                    self.file_path.name,
                )
                return False
            raise

        self._flushed_version = max(self._flushed_version, target_version)
        self.successful_writes_count += 1
        self.last_error = None
        return True

    async def start(self) -> None:
        """Inicia la tarea de fondo que sincroniza los cambios encolados."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="excel-sync-worker")

    async def stop(self, *, flush_remaining: bool = True, max_attempts: int = 3) -> None:
        """Detiene el worker e intenta persistir los cambios finales si los hubiera."""
        self._running = False
        self._wake_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if flush_remaining and self.is_dirty:
            for _ in range(max_attempts):
                if await self.flush_once():
                    break
                await asyncio.sleep(self.retry_interval_seconds)

    async def _run_loop(self) -> None:
        while self._running:
            await self._wake_event.wait()
            self._wake_event.clear()

            while self._running and self.is_dirty:
                flushed = await self.flush_once()
                if flushed:
                    # Si mientras escribíamos llegó otra versión, el bucle volverá a iterar
                    continue
                # Archivo bloqueado: esperar retry_interval_seconds antes de reintentar
                await asyncio.sleep(self.retry_interval_seconds)

    async def reload_inputs_if_available(self) -> WorkbookConfigSnapshot | None:
        """
        Relee la configuración de entrada (B1 y A:B) desde el disco para detectar
        ajustes en caliente del usuario, tolerando que el archivo esté abierto.
        """
        try:
            return await asyncio.to_thread(read_workbook_config, self.file_path)
        except (PermissionError, OSError) as exc:
            logger.debug("No se pudo releer Excel durante bloqueo temporal: %s", exc)
            return None
