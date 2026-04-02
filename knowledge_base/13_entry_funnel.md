# Concept: The Entry Funnel & Trade Management

## Definition
The 4-step conditional funnel that ALL price data must pass through before generating an order. If ANY step is missing, the state remains NO_TRADE. This is the synthesis of all previous concepts.

## The 4-Step Entry Funnel
### Step 1: Macro Analysis (Weekly/Daily)
- Determine "Expansion Intent" — is market targeting a major high or low?
- If Daily is Bullish → strictly disable Sell triggers (unless major HTF Supply Zone for counter-trend).

### Step 2: POI Layering (The Confluence Zone)
- Identify zones where multiple levels overlap: HTF FVG + HTF OB + Flip Zone.
- **Overlapping zones = "A+" POIs.** Ignore isolated levels if layered zone exists nearby.

### Step 3: Temporal Trigger (Session Window)
- Execution ONLY during London Open or New York Open windows.

### Step 4: LTF Confirmation (Finger on the Trigger)
- Switch to 5m or 1m timeframe.
- Wait for LTF Breaker Block inside HTF POI.
- Entry: Market order on BB candle close OR Limit order on retest.
- **Bias Trap Rule**: LTF Breaker forming OUTSIDE an HTF level has ~80% failure rate. Bot is ONLY authorized to scan for Breakers if price is currently interacting with an HTF level.

## Advanced Trade Management (Risk-Free Protocol)
### The 1:1 RR Milestone
When trade reaches 1:1 Reward-to-Risk:
1. **Partial Close**: Exit 50% of position volume.
2. **Break-Even**: Keep SL at original entry price (Risk-Free).
- Logic: 50% closed in profit → if remaining 50% hits original SL → net result = $0.
- Superior to moving SL to entry because allows price to "breathe" before final expansion.

### Trailing Logic
- As trade moves toward target, trail SL behind new structural lows/highs (Protected Levels) on LTF.

### Final TP
- Always at HTF External Liquidity (the high/low the daily bias is aiming for).
- If no specific liquidity nearby, use fixed 1:3 RR exit.

## Bot Logic Flowchart
1. Is time inside London/NY session? (Yes/No)
2. Is price above/below Daily Equilibrium (0.5 Fib)? (Yes/No)
3. Has price tapped untested Daily FVG or 4H OB? (Yes/No)
4. Has 5m Breaker Block closed in bias direction? (Yes/No)
5. Calculate lot size for $100 risk based on Breaker High/Low → Place Order.

## Counter-Trend Rules
- Shorting in Bullish trend permitted ONLY if price taps major HTF Supply Zone AND provides 5m BB.
- Risk must be reduced by 50% for counter-trend setups.

## Entry Rules
- All 4 funnel steps must pass.
- If price floats in "Middle Range" (no level hit) → STATE_IDLE.
- "The Waiting Game": If price enters HTF POI but no LTF BB forms → DO NOT enter.

## Session Context
- Step 3 restricts all execution to London and NY windows.
