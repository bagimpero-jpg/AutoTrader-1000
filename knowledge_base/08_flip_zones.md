# Concept: Flip Zones (FZ) & Momentum Entries

## Definition
A Flip Zone is a structural level where a previous High or Low acts as a turning point BEFORE price reaches a deep Order Block. Prevents the bot from missing trades while waiting for a deep retracement that may never be hit.

## Primary Rule
- **Flip Zones MUST only be traded in Trending Markets.** In sideways ranges, these levels are high-risk and prone to fakeouts.

## Marking Protocols
### Bullish Flip Zone (Buy Setup)
- Identify previous structural High that was just broken.
- Zone: From Highest Wick to Highest Body Close of that high point.

### Bearish Flip Zone (Sell Setup)
- Identify previous structural Low that was just broken.
- Zone: From Lowest Wick to Lowest Body Close of that low point.

## OB vs. FZ Synergy
- **Nesting**: If FZ and OB are nearly touching/overlapping → treat as Single POI. Confirmation anywhere in combined zone triggers entry.
- **Separation**: If visible gap between them → attempt FZ entry first (with confirmation). If FZ fails, reset and look for confirmation at OB.

## Entry Rules
1. Mark FZ on HTF (1H or 15m).
2. Wait for tap into FZ.
3. Switch to LTF (5m or 1m).
4. Wait for Breaker Block confirmation on LTF.
5. Enter on BB retest with 1:2 or 1:3 RR target.
- **Never enter blindly on FZ touch.**

## Exit Rules
- TP at next HTF External Liquidity.
- SL behind the Flip Zone.

## Special Rule: Opening Gaps
- Gaps at market open (Sundays/Mondays) → treat exactly like FVGs.
- Anticipate price returning to "fill" the gap before continuing HTF trend.

## Confluence Factors
- Strong trending market (clear HH/HL or LL/LH).
- FZ near an OB creates a combined high-probability zone.
- Momentum confirmation on LTF.

## Session Context
- Most effective during London and NY trending sessions.
- Avoid during Asian session (typically ranging).
