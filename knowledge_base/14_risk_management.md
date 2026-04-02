# Concept: Risk Management & Algorithmic Sizing

## Definition
The mathematical framework ensuring the bot passes the FTMO Challenge without hitting maximum drawdown. Position sizing is ALWAYS dynamic — never fixed lot sizes.

## The 1R Concept
- 1R = fixed capital risked per trade.
- **Formula**: `Lot Size = (Total Risk Amount) / (SL Distance in Pips * Pip Value)`
- Gold: 100 Quantity = 1.0 Standard Lot.

## Risk-to-Reward Targets
- **Bot Target**: Minimum 1:2, Ideal 1:3.
- With 1:3 RR, bot only needs 30% win rate to profit.
- Hasan's preferred zone: 40-60% win rate with 1:2 to 1:4 RR.

## The Risk-Free Protocol (Mandatory)
1. **1:1 Milestone**: When floating profit = initial risk amount (1:1 RR), trigger preservation.
2. **Partial Exit**: Close exactly 50% of position volume.
3. **Risk-Free State**: Keep SL at original entry level.
   - If remaining 50% hits SL → net result = $0 (breakeven).
4. **Why not just move SL to entry?** Closing 50% is superior — allows price to breathe and pull back without tagging BE stop before final expansion.

## Account-Phase Risk Levels
### Challenge Phase ($10k FTMO Evaluation)
- Risk per trade: Strictly 1% ($100).
- Quality over quantity. 1% ensures target hit without max drawdown.

### Live/Funded Phase (After Passing)
- Risk per trade: Drop to 0.5% ($50).
- Withdraw often. Secure 1-3% profit.

### Small Real Accounts (<$5,000)
- Risk per trade: 3-5%.
- Under $100: Use Cent Account ($1 = 100 units).

### Account Flipping (High-Risk Mode)
- ONLY with "House Money" (profits from main account).
- Risk per trade: 15-25%.
- Disable Risk-Free protocol. Full position hits TP or SL.

## FTMO Challenge Limits
- Max daily loss: 5% ($500) — hard stop, no new trades.
- Max total drawdown: 10% ($1,000) — challenge fails if breached.
- Target: 10% profit ($1,000) in 30 calendar days.

## Entry Rules
- Every trade must have lot size dynamically calculated.
- Risk ≤ 1% of current balance (challenge phase).
- Maximum 2-3 trades per day.

## Exit Rules
- At 1:1 RR: Close 50%, keep SL at entry.
- Trail SL behind new LTF protected levels as trade progresses.
- Final TP at HTF External Liquidity or fixed 1:3 RR.

## Session Context
- Risk rules apply uniformly across all sessions.
- During high-impact news: consider waiting for trap to clear before sizing.
