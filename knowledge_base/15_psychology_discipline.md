# Concept: Psychology, Discipline & Behavioral Guardrails

## Definition
Algorithmic safeguards against the three psychological failures that kill retail traders: impatience, greed, and fear. The bot's primary directive is to PROTECT the $10k capital.

## The Three Failure Points
1. **Lack of Patience**: Entering in the "middle" before price hits a POI.
2. **Greed**: Over-risking on a single trade to "get rich fast."
3. **Fear**: Hesitating on valid A+ setups after a loss streak.

## Dynamic Risk Scaling (Fear & Confidence)
### Confidence Mode (Standard)
- Risk 1% per trade during winning streak or stable performance.

### Recovery Mode (The Fear Fix)
- After 3 consecutive losses → automatically reduce risk to 0.25-0.50%.
- Logic: Continue "practicing" on live data without destroying account.
- Once winning streak resumes → return to 1%.

### Accountability Filter
- If bot's bias is 100% wrong (trying to sell while market is mooning) → enter State of Inactivity for 24 hours to "reset" perspective.

## The Breakeven Challenge (Preservation Rule)
- **Primary goal is NOT to make profit, but to NOT LOSE the account.**
- Bot must be optimized to close the week at Breakeven or higher.
- A single revenge trade can set back 6-7 months.

## Operational Guardrails
| Issue | Algorithmic Fix |
|-------|----------------|
| Over-trading | Max 2-3 trades per day |
| Early Exit | Only manual exit at 1:1 RR (close 50%); let rest hit TP/SL |
| Revenge Trading | Hard cap daily loss (e.g., -2% shuts down bot) |
| Bias Blindness | Require HTF tap before ANY LTF entry |

## Entry Rules
- No trade without POI + session window + LTF confirmation.
- No trade in "middle" — price must be at an extreme/level.
- Max 2-3 trades per day.

## Exit Rules
- If SL hit, it's a "business expense." No revenge.
- Do not interfere with bot during active trade.

## Gambling vs. Trading
- **Gambling**: Multiple small lots without POI or session window, hoping one hits.
- **Trading**: Specific HTF Level → LTF Breaker → 1:3 RR target.

## Session Context
- Applies across all sessions.
- Dead hours = mandatory inactivity.
