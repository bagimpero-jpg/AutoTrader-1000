# Concept: Fair Value Gaps (FVG) & Imbalances

## Definition
A 3-candle pattern indicating sudden institutional buying/selling pressure, leaving a "gap" where price was not efficiently delivered. FVGs are "magnetic" pending order zones representing Internal Range Liquidity.

## Identification Rules
- **Bullish FVG**: Gap between Candle 1's High and Candle 3's Low (Candle 3 Low > Candle 1 High).
- **Bearish FVG**: Gap between Candle 1's Low and Candle 3's High (Candle 1 Low > Candle 3 High).
- **Candle color and wick length do NOT matter** — only the price gap matters.

## Strategic Logic (Internal vs External Liquidity)
- FVG = Internal Range Liquidity (entry zone).
- Old Highs/Lows = External Range Liquidity (target).
- Flow: Price taps FVG (Internal) → anticipate move toward next major High/Low (External) as TP.

## Invalidation Rules
- FVG is "failed" if a candle closes completely outside the gap.
- **One-Time Use**: FVG is most effective on its First Tap only. Subsequent taps have higher failure risk.

## Entry Rules
- Never blindly place limit orders on an FVG.
- Must "nest" HTF FVG with LTF structure shift (CHoCH/BOS).
- **Timeframe Alignment**:
  - Monthly FVG → monitor Daily for structure break.
  - Weekly FVG → monitor 4H for structure break.
  - Daily FVG → monitor 1H for structure break.
  - 4H FVG → monitor 15-Minute for structure break.
  - 1H FVG → monitor 5-Minute for structure break.

## Bot Execution Flow
1. **Scan**: Identify untested HTF FVG (e.g., Daily).
2. **Alert**: Price enters the FVG zone → switch to LTF.
3. **Confirm**: Wait for Market Structure Shift on LTF.
4. **Execute**: Enter with 1% risk, SL behind LTF protected level, TP at HTF External Liquidity.

## Exit Rules
- TP at the nearest HTF External Liquidity (Old Highs/Lows or EQH/EQL).
- SL behind the LTF protected level.

## Session Context
- HTF FVGs form across all sessions.
- Execution only during London/NY when price taps the zone.
