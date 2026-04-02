# Concept: Market Sessions & Volume Windows

## Definition
The market moves when institutional banks operate. The bot must strictly restrict execution to high-volume session windows and use the session clock to overlay the AMD cycle.

## The Three Sessions
### 1. Asian Session (The Accumulation)
- **Time**: 00:00-08:00 UTC (04:00-08:00 PKT)
- **Characteristics**: Range/consolidation. Builds liquidity above and below.
- **Bot Logic**: Map Asian High and Asian Low as major liquidity targets for London/NY. NO execution.

### 2. London Session (The Manipulation/Trend Setter)
- **Time**: 08:00-12:00 UTC (11:00-14:00 PKT)
- **Characteristics**: Often creates HOD (High of Day) or LOD (Low of Day).
- **The Trap**: London frequently sweeps Asian High or Low before moving in the true Daily direction.
- **Bot Logic**: Look for Liquidity Sweep of Asian levels → LTF Breaker Block during this window.

### 3. New York Session (The Distribution/Expansion)
- **Time**: 13:00-17:00 UTC (17:00-20:00 PKT)
- **Characteristics**: Highest volume. Typically expansion (continuing London) or complete reversal.
- **Bot Logic**: If London set LOD/HOD, ride the trend toward final HTF target.

## AMD Session Overlay
- **Accumulation** = Asian Session
- **Manipulation** = London Open (sweeping Asian levels)
- **Distribution** = New York Session (expansion toward HTF targets)

## The 4-Layer Filter
1. **HTF Filter**: Is Daily/4H Bullish or Bearish?
2. **Level Filter**: Has price hit an HTF FVG, OB, or Demand zone?
3. **Time Filter**: Are we in London or NY session windows?
4. **Execution Filter**: Has an LTF Breaker Block formed after a session liquidity sweep?

## Example Bullish Setup
1. Daily is Bullish.
2. Asia forms a clean range.
3. London drops to sweep Asian Low → taps into 4H Demand Zone.
4. Bot identifies M5 Breaker Block during London open.
5. Target: Asian High or Daily External Liquidity.

## Entry Rules
- Execute ONLY during London or NY sessions.
- No trades during dead hours (12:00-13:00, after 17:00 UTC).
- On high-impact news days (NFP, CPI), wait for the session "Trap" to clear before looking for BB.

## Exit Rules
- TP at session external liquidity or HTF targets.

## Session Context
- Daylight savings shifts times by 1 hour (March/October) — bot must include dynamic offset.
- Crypto exception: Prioritize NY session for BTC (Wall Street drives volatility).
