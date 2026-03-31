# Concept: Market Structure & Structural Validation

## Definition
The rules for identifying trend direction through swing point validation, BOS (Break of Structure), and CHoCH (Change of Character). This is the "Anti-Trap" logic that prevents taking fake trades.

## The Three Market States
1. **Bullish**: Breaking Highs, Protecting Lows (HH + HL pattern).
2. **Bearish**: Breaking Lows, Protecting Highs (LL + LH pattern).
3. **Sideways/Range**: No clear structural breaks. Bot enters "Wait Mode."

## Structural Validation Rules (Anti-Trap Logic)
- **Higher Low (HL)**: NOT valid until price breaks AND closes above the previous Higher High (HH).
- **Lower High (LH)**: NOT valid until price breaks AND closes below the previous Lower Low (LL).
- **Critical**: If the bot sees a "Low" but price hasn't broken the "High" yet, flag as Internal Structure — do NOT place SL there.

## Protected vs. Targeted Levels
- **Protected Levels**: Validated HL (uptrend) or LH (downtrend). If broken → trend shift.
- **Targeted Levels**: Weak HH or LL that market is expected to run through.

## Entry Rules
- BOS (Break of Structure): Trend continuation — price breaks a validated High (bullish) or Low (bearish).
- CHoCH (Change of Character): First break of a Protected Level — immediately flip bias.
- SL must ALWAYS be placed behind a Validated Protected Level.
- Never enter before a new HL/LH is validated — that's "gambling on internal structure."

## Exit Rules
- If CHoCH occurs against the trade direction, exit immediately.
- TP at the next targeted level (external liquidity).

## Confluence Factors
- Confirmed BOS + validated swing points = high probability.
- Multiple timeframe alignment strengthens the signal.

## Session Context
- Structure analysis applies across all timeframes and sessions.
- HTF structure (Daily/4H) determines bias; LTF structure (5m/1m) confirms entries.
