"""Interfaz de línea de comandos (CLI) con panel en vivo Rich para ClockAgent."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Sequence

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from clock_agent.browser.cdp_client import (
    BrowserConnectionError,
    CDPBrowserClient,
    SessionNotAuthenticatedError,
    build_browser_launch_command,
)
from clock_agent.excel.workbook_io import create_excel_template
from clock_agent.models.auction import WatchLot
from clock_agent.orchestrator import AuctionOrchestrator
from clock_agent.strategy.ledger import BudgetLedger


def build_dashboard_renderable(lots: list[WatchLot], ledger: BudgetLedger) -> Group:
    """Construye el panel de presupuesto y la tabla de relojes para Rich."""
    snap = ledger.snapshot()
    budget_summary = (
        f"[bold cyan]Presupuesto Global:[/bold cyan] {snap.global_budget_eur} €   |   "
        f"[bold green]Gastado (Ganados):[/bold green] {snap.spent_eur} €   |   "
        f"[bold yellow]Comprometido (Líder):[/bold yellow] {snap.committed_leader_eur} €   |   "
        f"[bold white]Saldo Disponible:[/bold white] {snap.available_eur} €"
    )
    budget_panel = Panel(budget_summary, title="ClockAgent · Contabilidad Atómica", border_style="cyan")

    table = Table(expand=True, show_lines=False)
    table.add_column("Fila", justify="right", style="dim", width=5)
    table.add_column("Lote / Título", overflow="fold")
    table.add_column("Estado", width=26)
    table.add_column("Tiempo", justify="right", width=10)
    table.add_column("Actual", justify="right", width=10)
    table.add_column("Mi Puja", justify="right", width=10)
    table.add_column("Techo E_i", justify="right", width=10)
    table.add_column("Máx M_i", justify="right", width=10)
    table.add_column("Reserva", width=14)

    for lot in lots:
        rem = (
            f"{int(lot.seconds_remaining)}s"
            if lot.seconds_remaining is not None and not lot.is_closed
            else "-"
        )
        title_display = lot.title or lot.lot_id
        table.add_row(
            str(lot.row_index or "-"),
            title_display,
            lot.status.value,
            rem,
            f"{lot.current_bid_eur} €",
            f"{lot.my_last_bid_eur} €",
            f"{lot.effective_ceiling_eur} €",
            f"{lot.max_bid_eur} €",
            lot.reserve_status.value,
        )

    return Group(budget_panel, table)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clock-agent",
        description=(
            "Agente autónomo de pujas de relojes en Catawiki controlado por Excel (.xlsx) "
            "con contabilidad atómica de presupuesto y conexión CDP a navegador autenticado."
        ),
    )
    parser.add_argument(
        "excel_pos",
        nargs="?",
        help="Ruta al archivo Excel (.xlsx) de control de subastas.",
    )
    parser.add_argument(
        "--excel",
        "-e",
        dest="excel_opt",
        help="Ruta al archivo Excel (.xlsx) de control de subastas.",
    )
    parser.add_argument(
        "--init-excel",
        metavar="RUTA_XLSX",
        help="Crea una plantilla Excel (.xlsx) lista para usar en la ruta indicada y termina.",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=1500,
        help="Presupuesto global inicial en EUR al generar plantilla con --init-excel (por defecto: 1500).",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=9222,
        help="Puerto local de depuración remota CDP de Chrome/Edge (por defecto: 9222).",
    )
    parser.add_argument(
        "--cdp-url",
        default=None,
        help="URL completa del endpoint CDP (por defecto: http://127.0.0.1:<cdp-port>).",
    )
    parser.add_argument(
        "--snipe-seconds",
        type=float,
        default=25.0,
        help="Segundos finales antes del cierre para ejecutar pujas y escalera de reserva (por defecto: 25).",
    )
    parser.add_argument(
        "--assault-seconds",
        type=float,
        default=300.0,
        help="Umbral en segundos para abrir pestaña dedicada en Modo Asalto (por defecto: 300).",
    )
    parser.add_argument(
        "--print-browser-cmd",
        choices=["chrome", "edge"],
        help="Muestra el comando para abrir Chrome o Edge con depuración remota CDP en Windows y termina.",
    )
    return parser


async def _run_agent_async(
    excel_path: Path,
    *,
    cdp_url: str,
    snipe_seconds: float,
    assault_seconds: float,
    console: Console,
) -> int:
    cdp_client = CDPBrowserClient(cdp_url=cdp_url)
    try:
        orchestrator = AuctionOrchestrator(
            excel_path,
            cdp_client=cdp_client,
            snipe_window_seconds=snipe_seconds,
            assault_threshold_seconds=assault_seconds,
        )
    except Exception as exc:
        console.print(f"[bold red]Error al cargar el fichero Excel:[/bold red] {exc}")
        return 1

    try:
        with Live(
            build_dashboard_renderable(orchestrator.lots, orchestrator.ledger),
            console=console,
            refresh_per_second=4,
        ) as live:
            orchestrator.on_state_change = lambda lots, ledger: live.update(
                build_dashboard_renderable(lots, ledger)
            )
            await orchestrator.run_until_complete()
    except (BrowserConnectionError, SessionNotAuthenticatedError) as exc:
        console.print(f"\n[bold red]Requisito de navegador/sesión:[/bold red] {exc}")
        console.print(
            f"[yellow]Sugerencia para abrir el navegador:[/yellow]\n  {build_browser_launch_command()}"
        )
        return 2
    except KeyboardInterrupt:
        orchestrator.request_stop()
        console.print("\n[yellow]Parada solicitada por el usuario.[/yellow]")
    finally:
        await cdp_client.close()

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.print_browser_cmd:
        cmd = build_browser_launch_command(port=args.cdp_port, browser=args.print_browser_cmd)
        console.print(cmd)
        return 0

    if args.init_excel:
        target = create_excel_template(
            args.init_excel,
            default_budget_eur=args.budget,
            sample_rows=[
                ("https://www.catawiki.com/es/l/12345678-ejemplo-reloj-omega", 450),
            ],
        )
        console.print(
            f"[bold green]Plantilla Excel creada correctamente en:[/bold green] {target.resolve()}"
        )
        return 0

    excel_target = args.excel_opt or args.excel_pos
    if not excel_target:
        parser.print_help()
        return 0

    cdp_url = args.cdp_url or f"http://127.0.0.1:{args.cdp_port}"
    exit_code = asyncio.run(
        _run_agent_async(
            Path(excel_target),
            cdp_url=cdp_url,
            snipe_seconds=args.snipe_seconds,
            assault_seconds=args.assault_seconds,
            console=console,
        )
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
