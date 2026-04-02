# Concept: Liquidity Pools & The Anti-Trap Rule

## Definition
The four primary liquidity pool types where retail stop-losses concentrate, and the critical "2nd Break Rule" that prevents the bot from mistaking liquidity sweeps for trend shifts.

## The 4 Primary Liquidity Pools
1. **Major Highs & Lows**: Previous Day/Week extremes.
2. **Ranging Markets (Consolidation)**: Lateral price movement building orders on both sides.
3. **Equal Highs (EQH) & Equal Lows (EQL)**: Retail "Double Tops/Bottoms" — act as magnetic targets.
4. **News Ranges**: Tight ranges formed 15-30 minutes before high-impact news (CPI, NFP).

## The 2nd Break Rule (Anti-Trap)
- **Problem**: First break of a validated low could be a liquidity sweep (manipulation), NOT a trend shift.
- **Logic**: `IF (Price breaks Low_A) THEN (Status = Pending_Bias_Shift). IF (Price creates Low_B AND breaks it) THEN (Status = Trend_Confirmed_Bearish).`
- **If price reclaims the range after breaking** → maintain original bias.

## Entry Rules
- In a Bullish trend: only buy "Range Low Sweeps." Ignore "Range High Breakouts" initially.
- Wait for a "Second Break" before flipping bias.
- Never trade the first break of a level in isolation.

## Exit Rules
- When price reaches EQH/EQL → this is the final TP zone. Market is expected to reverse or pause after clearing.
- Institutional orders move from one pool to the next — hold until liquidity is cleared.

## Confluence Factors
- Liquidity sweep + HTF POI tap = high probability.
- Equal Highs/Lows as TP targets increase reliability.
- News ranges as liquidity magnets before major moves.

## Session Context
- Asian session builds liquidity pools (range highs/lows).
- London sweeps Asian liquidity.
- NY expands toward final HTF targets.
