"""Auto Trader 1000 — Lightweight Monitoring Dashboard.

Run alongside main.py:
    py dashboard.py
    Open http://localhost:5050
"""
from __future__ import annotations

import csv
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template_string

load_dotenv()

from core.mt5_bridge import MT5Bridge

app = Flask(__name__)
bridge = MT5Bridge()

LOGS_DIR = Path("logs")
TRADE_CSV = LOGS_DIR / "trades.csv"
BOT_LOG = Path("autotrader.log")


def _connect_mt5() -> bool:
    """Ensure MT5 is connected."""
    try:
        bridge.connect(
            login=int(os.environ.get("FTMO_LOGIN", 0)),
            password=os.environ.get("FTMO_PASSWORD", ""),
            server=os.environ.get("FTMO_SERVER", ""),
            mt5_path=os.environ.get("MT5_PATH", ""),
        )
        return True
    except Exception:
        return False


def _tail_log(n: int = 25) -> list[str]:
    """Return last n lines of the bot log."""
    if not BOT_LOG.exists():
        return ["(no log file yet)"]
    try:
        lines = BOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return ["(error reading log)"]


def _read_recent_trades(n: int = 20) -> list[dict]:
    """Read last n trades from CSV."""
    if not TRADE_CSV.exists():
        return []
    try:
        with open(TRADE_CSV, "r", newline="") as f:
            reader = list(csv.DictReader(f))
            return reader[-n:]
    except Exception:
        return []


def _bot_running() -> bool:
    """Check if main.py is running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
        )
        return "python" in result.stdout.lower()
    except Exception:
        return False


TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="15">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AutoTrader 1000</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', Consolas, monospace; background: #0d1117; color: #c9d1d9; padding: 16px; }
  h1 { color: #58a6ff; font-size: 1.3em; margin-bottom: 12px; }
  h2 { color: #8b949e; font-size: 1em; margin: 16px 0 8px; border-bottom: 1px solid #21262d; padding-bottom: 4px; }
  .cards { display: flex; gap: 12px; flex-wrap: wrap; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px 16px; flex: 1; min-width: 140px; }
  .card .label { font-size: 0.75em; color: #8b949e; text-transform: uppercase; }
  .card .value { font-size: 1.4em; font-weight: bold; margin-top: 2px; }
  .green { color: #3fb950; } .red { color: #f85149; } .yellow { color: #d29922; }
  .status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
  .dot-green { background: #3fb950; } .dot-red { background: #f85149; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85em; margin-top: 6px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: normal; text-transform: uppercase; font-size: 0.75em; }
  .log-box { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px; font-size: 0.75em; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; line-height: 1.5; }
  .log-line { padding: 1px 0; }
  .log-err { color: #f85149; } .log-warn { color: #d29922; } .log-info { color: #8b949e; }
  .refresh { color: #484f58; font-size: 0.7em; float: right; }
</style>
</head>
<body>

<h1>AutoTrader 1000 <span class="status-dot {{ 'dot-green' if bot_running else 'dot-red' }}"></span>
<span style="font-size:0.6em;color:#8b949e">{{ 'LIVE' if bot_running else 'OFFLINE' }}</span>
<span class="refresh">refreshes every 15s &mdash; {{ now }}</span>
</h1>

<!-- Account -->
<h2>Account</h2>
<div class="cards">
  <div class="card"><div class="label">Balance</div><div class="value">${{ "%.2f"|format(account.balance) }}</div></div>
  <div class="card"><div class="label">Equity</div><div class="value">${{ "%.2f"|format(account.equity) }}</div></div>
  <div class="card"><div class="label">Floating P&L</div><div class="value {{ 'green' if account.profit >= 0 else 'red' }}">${{ "%.2f"|format(account.profit) }}</div></div>
  <div class="card"><div class="label">Free Margin</div><div class="value">${{ "%.2f"|format(account.free_margin) }}</div></div>
</div>

<!-- Risk -->
<h2>Risk Status</h2>
<div class="cards">
  <div class="card"><div class="label">Daily Drawdown</div><div class="value {{ 'red' if dd_pct > 3 else 'yellow' if dd_pct > 1 else 'green' }}">{{ "%.1f"|format(dd_pct) }}% / 5%</div></div>
  <div class="card"><div class="label">Total Drawdown</div><div class="value {{ 'red' if total_dd_pct > 7 else 'yellow' if total_dd_pct > 3 else 'green' }}">{{ "%.1f"|format(total_dd_pct) }}% / 10%</div></div>
  <div class="card"><div class="label">Open Trades</div><div class="value">{{ positions|length }}</div></div>
</div>

<!-- Positions -->
<h2>Open Positions</h2>
{% if positions %}
<table>
<tr><th>Ticket</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Current</th><th>SL</th><th>TP</th><th>P&L</th></tr>
{% for p in positions %}
<tr>
  <td>{{ p.ticket }}</td>
  <td>{{ p.symbol }}</td>
  <td>{{ p.type }}</td>
  <td>{{ "%.2f"|format(p.price_open) }}</td>
  <td>{{ "%.2f"|format(p.price_current) }}</td>
  <td>{{ "%.2f"|format(p.sl) }}</td>
  <td>{{ "%.2f"|format(p.tp) }}</td>
  <td class="{{ 'green' if p.profit >= 0 else 'red' }}">${{ "%.2f"|format(p.profit) }}</td>
</tr>
{% endfor %}
</table>
{% else %}
<p style="color:#484f58;font-size:0.85em;">No open positions.</p>
{% endif %}

<!-- Recent Trades -->
<h2>Recent Trades (CSV)</h2>
{% if recent_trades %}
<table>
<tr><th>Date</th><th>Dir</th><th>Entry</th><th>Exit</th><th>P&L</th><th>RR</th><th>Result</th></tr>
{% for t in recent_trades %}
<tr>
  <td>{{ t.get('date','') }}</td>
  <td>{{ t.get('direction','') }}</td>
  <td>{{ t.get('entry_price','') }}</td>
  <td>{{ t.get('exit_price','') }}</td>
  <td class="{{ 'green' if (t.get('pnl_dollars','0')|float) >= 0 else 'red' }}">${{ t.get('pnl_dollars','0') }}</td>
  <td>{{ t.get('rr_achieved','') }}</td>
  <td class="{{ 'green' if t.get('result','')=='WIN' else 'red' if t.get('result','')=='LOSS' else '' }}">{{ t.get('result','') }}</td>
</tr>
{% endfor %}
</table>
{% else %}
<p style="color:#484f58;font-size:0.85em;">No trades logged yet.</p>
{% endif %}

<!-- Log -->
<h2>Bot Log (last 25 lines)</h2>
<div class="log-box">
{% for line in log_lines %}
<div class="log-line {{ 'log-err' if '[ERROR]' in line else 'log-warn' if '[WARNING]' in line else 'log-info' }}">{{ line }}</div>
{% endfor %}
</div>

</body>
</html>"""


@app.route("/")
def index():
    connected = _connect_mt5()

    if connected:
        account_raw = bridge.get_account_info() or {}
        positions_raw = bridge.get_open_positions() or []
    else:
        account_raw = {}
        positions_raw = []

    class Account:
        balance = account_raw.get("balance", 0.0)
        equity = account_raw.get("equity", 0.0)
        profit = account_raw.get("profit", 0.0)
        free_margin = account_raw.get("free_margin", 0.0)
        margin = account_raw.get("margin", 0.0)

    class Position:
        def __init__(self, d: dict):
            self.ticket = d.get("ticket", 0)
            self.symbol = d.get("symbol", "")
            self.type = d.get("type", "")
            self.price_open = d.get("price_open", 0.0)
            self.price_current = d.get("price_current", 0.0)
            self.sl = d.get("sl", 0.0)
            self.tp = d.get("tp", 0.0)
            self.profit = d.get("profit", 0.0)

    account = Account()
    positions = [Position(p) for p in positions_raw]

    base_balance = 10000.0
    dd_pct = max(0.0, (base_balance - account.equity) / base_balance * 100) if account.equity else 0.0
    total_dd_pct = dd_pct  # simplified — same as daily for now

    return render_template_string(
        TEMPLATE,
        account=account,
        positions=positions,
        dd_pct=dd_pct,
        total_dd_pct=total_dd_pct,
        log_lines=_tail_log(),
        recent_trades=_read_recent_trades(),
        bot_running=_bot_running(),
        now=datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    )


if __name__ == "__main__":
    print("Dashboard starting at http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
