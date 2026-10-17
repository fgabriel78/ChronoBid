# ChronoBid

Autonomous watch bidding agent for **Catawiki** controlled via a background **Excel (`.xlsx`)** spreadsheet, featuring atomic budget accounting in euros, a dynamic portfolio optimizer to maximize the number of awarded watches, and a connection via **Chrome DevTools Protocol (CDP)** using the user's authenticated session.

---

## Key Features

1. **Background Excel (`.xlsx`) Control & Reporting**:
   - Define your **Global Budget (EUR)** in cell `B1`, and each watch's **Catawiki URL** and **Clean Maximum Bid (EUR)** in columns `A` and `B` (starting from row 7).
   - `ExcelSyncWorker` automatically updates the spent budget (`B2`), committed budget as leading bidder (`B3`), available balance (`B4`), and status columns (`C:J`), tolerating file locks (`PermissionError`) when the spreadsheet is open in Microsoft Excel.
2. **Transparent Connection to Authenticated Browser (CDP)**:
   - Connects to your real Chrome or Edge instance (`--remote-debugging-port=9222`), reusing your logged-in Catawiki session without exposing credentials or triggering anti-bot protections.
3. **Two Operating Speeds**:
   - **Patrol Mode (> 5 min)**: Lightweight periodic monitoring and automatic pruning of watches that exceed your maximum bid before closing.
   - **Assault / Snipe Mode (≤ 5 min and +90s extensions)**: Dedicated real-time tab for each lot in its final minutes.
4. **Assault Tactics & Reserve Price Ladder**:
   - **Invisibility until `< 25s`**: Does not reveal your interest or upper ceiling before the final 25 seconds.
   - **Minimum increment bidding**: Always bids the next minimum required step on Catawiki.
   - **+90-second loop**: Counter-attacks during extensions as long as the next minimum step does not exceed the Dynamic Ceiling ($E_i$) or available balance.
   - **Reserve Price Ladder**: If you are already the highest bidder in the last 25s but the *"Reserve price not met"* notice persists, steps up increment by increment and stops the exact instant the notice disappears.
5. **Dynamic Portfolio Optimizer (`PortfolioOptimizer`) & Atomic `BudgetLedger`**:
   - Guarantees via `asyncio.Lock` that the sum of won watches plus bids where you are currently leading never exceeds the global budget.
   - Dynamically adjusts the Effective Ceiling ($E_i \le M_i$), reserving capital when a combination of 2 or more future affordable watches with high headroom allows winning more watches with the same budget.

---

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

---

## Quick Start Guide

### 1. Generate the Excel template

```powershell
chronobid --init-excel relojes.xlsx --budget 1500
```

Open `relojes.xlsx`, set the **Global Budget (EUR)** in cell `B1`, and add the **Catawiki lot URL** (column `A`) and your **Clean Maximum Bid in euros** (column `B`) starting from row `7`.

### 2. Launch your browser with remote debugging (CDP) and log into Catawiki

You can get the exact Windows command by running:

```powershell
chronobid --print-browser-cmd chrome
```

Or run directly in **PowerShell** (note the `&` operator at the beginning for quoted paths):

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:LOCALAPPDATA\ChronoBid\chrome-profile" https://www.catawiki.com/es/
```

Log in normally to your Catawiki account within that browser window.

### 3. Launch the agent

```powershell
chronobid --excel relojes.xlsx --cdp-port 9222
```

---

## Running Tests

```powershell
pytest -v
```
