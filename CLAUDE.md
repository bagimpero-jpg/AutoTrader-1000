# Auto Trader 1000 — Project Rules

## Language & Style
- Always write modular, object-oriented Python 3.10+.
- Use type hints everywhere. Prefer dataclasses/Pydantic models for data structures.
- Each module lives in its own file under a clear package hierarchy.

## Core Strategy — Smart Money Concepts (SMC)
Based on Kinetic Traders / Hasan's methodology:
- **Breaker Blocks** — failed order blocks that flip into support/resistance.
- **Order Blocks** — institutional candle zones where large orders were placed.
- **Fair Value Gaps (FVGs)** — imbalance zones (3-candle pattern) that price tends to revisit.
- **BOS / CHOCH** — Break of Structure / Change of Character for trend confirmation.
- **AMD (Accumulation, Manipulation, Distribution)** — session-based market phases.
- **Liquidity Sweeps** — stop-hunt wicks beyond key highs/lows.
- **Range Trading** — identify consolidation, trade the breakout.
- **Fibonacci** — retracement and extension levels for entry/TP refinement.
- **Flip Zones** — areas where old support becomes resistance and vice versa.

## Risk Management
- **1% risk per trade** on a $10,000 base (max $100 risk per position).
- **Minimum 1:2 Risk-to-Reward ratio** — never enter a trade below this threshold.
- **Max daily drawdown**: 5% ($500) — hard stop, no new trades for the day.
- **Max total drawdown**: 10% ($1,000) — FTMO challenge limit.
- Lot size is dynamically calculated from SL distance and account equity.

## Trading Sessions (UTC)
- **Asian Session (00:00–08:00 UTC)**: NO execution. Used only for liquidity profiling, range identification, and marking key levels.
- **London Session (08:00–12:00 UTC)**: Primary execution window. Look for manipulation of Asian highs/lows.
- **New York Session (13:00–17:00 UTC)**: Secondary execution window. Look for London continuation or reversal setups.
- **Dead zones (12:00–13:00, after 17:00 UTC)**: No new entries.

## FTMO Challenge Rules
- Profit target: 10% ($1,000) in 30 calendar days.
- Max daily loss: 5% ($500).
- Max total loss: 10% ($1,000).
- Minimum 4 trading days.
- No trading during prohibited news events (NFP, FOMC, etc.).

## Architecture
```
AutoTrader 1000/
├── CLAUDE.md
├── .env                    # Credentials (NEVER read/commit)
├── config/
│   └── settings.yaml       # Runtime config (pairs, sessions, risk params)
├── core/
│   ├── mt5_bridge.py       # MT5 connection manager
│   ├── order_manager.py    # Order execution & management
│   └── state_manager.py    # Local JSON state persistence
├── strategy/
│   ├── smc_engine.py       # Main SMC analysis engine
│   ├── structures.py       # BOS/CHOCH detection
│   ├── zones.py            # OB, FVG, Breaker, Flip zone detection
│   ├── liquidity.py        # Liquidity sweep detection
│   └── session_profiler.py # Session-based analysis (AMD)
├── knowledge_base/
│   └── *.md                # Hasan's transcript-derived trade rules
├── risk/
│   ├── risk_manager.py     # Position sizing, drawdown checks
│   └── news_filter.py      # High-impact news event filter
├── cloud/
│   ├── cloud_logger.py     # MCP/Google Sheets/Postgres logger
│   └── trade_journal.py    # Structured trade journal entries
├── reflection/
│   ├── self_reflection.py  # Post-trade analysis & pattern detection
│   └── strategy_tuner.py   # Parameter adjustment proposals
├── tests/
│   └── ...
└── main.py                 # Entry point / orchestrator
```

## Git Workflow
- Feature branches: `feature-<module-name>`
- Commit often. Each module gets its own branch via worktrees.
- Never commit `.env` or credentials.
