# Concept: Power of Three (AMD) — Accumulation, Manipulation, Distribution

## Definition
The master logic for how institutional price moves develop. Markets follow a 3-step ritual to ensure enough liquidity fuels expansion. The market does this 60-70% of the time.

## The Three Phases
### 1. Accumulation (The Range)
- Sideways consolidation in a tight range.
- Builds liquidity (stop losses) above and below.
- **Bot Status: IDLE** — Map Range High/Low. Do NOT trade inside this phase.

### 2. Manipulation (The Trap/Sweep)
- Sudden fakeout move, usually OPPOSITE to the HTF direction.
- Sweeps liquidity built during Accumulation.
- Price manipulates into an HTF POI (Weekly/Daily FVG or OB).
- **Bot Status: ALERT** — Wait for sweep of range low/high to enter HTF level.

### 3. Distribution (The Expansion)
- The REAL impulsive move in the intended direction. Highest volume.
- **Bot Status: ACTIVE** — Execute trade on LTF Breaker Block retest.

## Technical Confirmation (Manipulation → Distribution)
1. **The Sweep**: Price must clear Highs or Lows of the Accumulation range.
2. **The Level Tap**: Price must touch an HTF Level (e.g., Weekly FVG).
3. **The Reclaim**: Price moves back inside range OR forms LTF Breaker Block.
- **If price breaks out and stays without forming a BB** → may be Trend Continuation, not manipulation. Wait for LTF Breaker.

## Entry Rules
- Only enter during Distribution phase.
- Requires: Range swept + HTF level tapped + LTF Breaker Block confirmed.
- Trade the extremes of the range, NEVER the middle.

## Exit Rules
- TP at opposite range boundary or HTF External Liquidity.
- The 50% Rule: Range midpoint can be "TP1" or Break Even zone.

## The Three W's (Logging Requirement)
Before every trade, bot must log:
1. **What** is happening? (e.g., Price consolidating after dump)
2. **Where** is it coming from/going? (e.g., Tapped Weekly FVG → going toward Daily High)
3. **Why** is it reacting? (e.g., Swept range liquidity to fuel expansion)

## Market Phase Detection
- Low volatility + sideways price → MARKET_ACCUMULATION
- Price spikes outside 24h range into HTF FVG → MARKET_MANIPULATION
- 5m Breaker Block forms opposite to spike → EXECUTE_DISTRIBUTION

## Session Context
- Accumulation typically = Asian Session.
- Manipulation typically = London Open.
- Distribution typically = New York Session.
