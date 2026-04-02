# Concept: Fibonacci & Optimal Trade Entry (OTE)

## Definition
Fibonacci is NOT a standalone strategy — it's a Mathematical Confluence tool to measure "Healthy Retracements" and filter which of multiple POIs to trade.

## The Golden Levels (Exact Values)
- **0.0 (Zero)**: End of the impulse move.
- **0.5 (Equilibrium/50%)**: Midpoint. NO trades should be taken BEFORE price reaches this level.
- **0.618 (61.8%)**: Start of the High-Probability Zone.
- **0.66 (66%)**: The "Ideal" entry level (Hasan's specific OTE tweak).
- **1.0 (One)**: Start of impulse move (Invalidation Point).

## Premium vs. Discount Logic
### Bullish Setup (Buying at Discount)
- Draw Fib from Swing Low to Swing High.
- Price must drop BELOW 0.5 level (entering "Discount" zone).
- Bot looks for OB/FVG/Flip Zone between 0.618 and 0.66.
- SL: Behind 1.0 level or most recent validated Low.

### Bearish Setup (Selling at Premium)
- Draw Fib from Swing High to Swing Low.
- Price must rise ABOVE 0.5 level (entering "Premium" zone).
- Bot looks for Bearish POI (OB/FVG/Supply) between 0.618 and 0.66.
- SL: Behind 1.0 level or most recent validated High.

## Momentum Confirmation (Candle Rule)
1. Price enters 0.618-0.66 zone.
2. Wait for a candle of intended direction to CLOSE on entry TF (e.g., 1H green candle for Buy).
3. Place SL at candle's wick low → tighter SL = higher RR.

## Entry Rules
- Only use Fib on clear "Impulse Legs" (sharp moves that broke structure). Useless in sideways.
- Use Major Highs/Lows for Fib leg — internal minor highs/lows cause false signals.
- Only draw Bullish Fibs if Daily bias is Bullish (and vice versa).
- **Conflict Resolution**: If perfect Breaker at 0.382 (outside zone) vs. mediocre OB at 0.618 → prioritize the level INSIDE the Fib OTE zone.

## Exit Rules
- TP at HTF External Liquidity.
- Minimum 1:2 RR.

## Confluence Factors
- POI (OB/FVG/FZ) sitting in the 0.618-0.66 zone = highest probability.
- Multiple overlapping levels within OTE zone = A++ setup.

## Session Context
- Fib levels apply across all timeframes.
- Execution only during London/NY sessions.
