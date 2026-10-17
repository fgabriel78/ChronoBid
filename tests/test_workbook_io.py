"""Tests para el generador, lector y escritor de Excel en workbook_io.py."""

from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from clock_agent.excel.workbook_io import (
    FIRST_DATA_ROW,
    create_excel_template,
    read_workbook_config,
    write_workbook_state,
)
from clock_agent.models.auction import (
    AuctionStatus,
    GlobalBudgetState,
    ReserveStatus,
    WatchLot,
)


def test_create_template_and_read_valid_rows(tmp_path: Path) -> None:
    excel_path = tmp_path / "relojes.xlsx"
    sample_rows = [
        ("https://www.catawiki.com/es/l/100001-omega-constellation", 600),
        ("https://www.catawiki.com/es/l/100002-seiko-presage", 350),
    ]
    create_excel_template(excel_path, default_budget_eur=1200, sample_rows=sample_rows)
    assert excel_path.exists()

    snapshot = read_workbook_config(excel_path)
    assert snapshot.global_budget_eur == 1200
    assert len(snapshot.lots) == 2

    lot1, lot2 = snapshot.lots
    assert lot1.lot_id == "lot-100001"
    assert lot1.max_bid_eur == 600
    assert lot1.row_index == FIRST_DATA_ROW
    assert lot1.status == AuctionStatus.PENDING

    assert lot2.lot_id == "lot-100002"
    assert lot2.max_bid_eur == 350
    assert lot2.row_index == FIRST_DATA_ROW + 1


def test_read_workbook_detects_invalid_rows_and_continues(tmp_path: Path) -> None:
    excel_path = tmp_path / "relojes_mixto.xlsx"
    create_excel_template(excel_path, default_budget_eur=800)

    wb = load_workbook(excel_path)
    ws = wb.active
    assert ws is not None
    # Fila 7: válida
    ws.cell(row=7, column=1, value="https://www.catawiki.com/es/l/200001-longines")
    ws.cell(row=7, column=2, value=400)
    # Fila 8: sin URL
    ws.cell(row=8, column=1, value="")
    ws.cell(row=8, column=2, value=250)
    # Fila 9: puja máxima negativa
    ws.cell(row=9, column=1, value="https://www.catawiki.com/es/l/200003-tissot")
    ws.cell(row=9, column=2, value=-10)
    # Fila 10: puja máxima no numérica
    ws.cell(row=10, column=1, value="https://www.catawiki.com/es/l/200004-hamilton")
    ws.cell(row=10, column=2, value="abc")
    # Fila 11: vacía (debe ignorarse)
    # Fila 12: válida con símbolo de euro
    ws.cell(row=12, column=1, value="https://www.catawiki.com/es/l/200005-certina")
    ws.cell(row=12, column=2, value="275 €")
    wb.save(excel_path)
    wb.close()

    snapshot = read_workbook_config(excel_path)
    assert snapshot.global_budget_eur == 800
    assert len(snapshot.lots) == 5

    assert snapshot.lots[0].status == AuctionStatus.PENDING
    assert snapshot.lots[0].max_bid_eur == 400

    assert snapshot.lots[1].status == AuctionStatus.VALIDATION_ERROR
    assert snapshot.lots[1].row_index == 8

    assert snapshot.lots[2].status == AuctionStatus.VALIDATION_ERROR
    assert snapshot.lots[2].row_index == 9

    assert snapshot.lots[3].status == AuctionStatus.VALIDATION_ERROR
    assert snapshot.lots[3].row_index == 10

    assert snapshot.lots[4].status == AuctionStatus.PENDING
    assert snapshot.lots[4].max_bid_eur == 275
    assert snapshot.lots[4].row_index == 12


def test_read_workbook_invalid_global_budget_raises(tmp_path: Path) -> None:
    excel_path = tmp_path / "presupuesto_invalido.xlsx"
    create_excel_template(excel_path, default_budget_eur=500)
    wb = load_workbook(excel_path)
    ws = wb.active
    assert ws is not None
    ws["B1"].value = 0
    wb.save(excel_path)
    wb.close()

    with pytest.raises(ValueError, match="Presupuesto Global"):
        read_workbook_config(excel_path)


def test_write_workbook_state_updates_outputs_and_preserves_user_inputs(tmp_path: Path) -> None:
    excel_path = tmp_path / "estado.xlsx"
    sample_rows = [
        ("https://www.catawiki.com/es/l/300001-omega", 500),
    ]
    create_excel_template(excel_path, default_budget_eur=1000, sample_rows=sample_rows)

    # Simulamos que el usuario edita manualmente B1 y B7 en el disco antes de que el agente escriba
    wb = load_workbook(excel_path)
    ws = wb.active
    assert ws is not None
    ws["B1"].value = 1250
    ws["B7"].value = 550
    wb.save(excel_path)
    wb.close()

    lot = WatchLot(
        lot_id="lot-300001",
        url="https://www.catawiki.com/es/l/300001-omega",
        max_bid_eur=500,
        row_index=7,
        title="Omega Geneve Automatic 1972",
        closing_time=datetime(2026, 10, 17, 21, 15, 0),
        status=AuctionStatus.LEADER,
        my_last_bid_eur=320,
        current_bid_eur=320,
        reserve_status=ReserveStatus.MET,
        dynamic_ceiling_eur=480,
        notes="Puja líder aceptada",
    )
    budget = GlobalBudgetState(
        global_budget_eur=1000,
        spent_eur=200,
        committed_leader_eur=320,
    )

    write_workbook_state(excel_path, budget, [lot])

    wb_check = load_workbook(excel_path, data_only=True)
    ws_check = wb_check.active
    assert ws_check is not None
    # Entradas de usuario preservadas intactas
    assert ws_check["B1"].value == 1250
    assert ws_check["A7"].value == "https://www.catawiki.com/es/l/300001-omega"
    assert ws_check["B7"].value == 550

    # Salidas de presupuesto actualizadas
    assert ws_check["B2"].value == 200
    assert ws_check["B3"].value == 320
    assert ws_check["B4"].value == 480

    # Columnas C..J actualizadas
    assert ws_check["C7"].value == "Omega Geneve Automatic 1972"
    assert ws_check["D7"].value == "2026-10-17 21:15:00"
    assert ws_check["E7"].value == "LIDER"
    assert ws_check["F7"].value == 320
    assert ws_check["G7"].value == 320
    assert ws_check["H7"].value == "ALCANZADA"
    assert ws_check["I7"].value == 480
    assert ws_check["J7"].value == "Puja líder aceptada"
    wb_check.close()
