# Concept: Pip Measurement & Position Sizing

## Definition
The standardized pip calculation rules for different instruments and the lot sizing system for the FTMO $10k challenge.

## Pip Standards
- **XAUUSD (Gold)**: $1.00 move = 10 pips. $2000→$2010 = 100 pips.
- **Forex Pairs**: 4th decimal place = 1 pip. 1.1000→1.1001 = 1 pip.
- **BTC**: $100 move = 10 pips.

## Lot Sizes
- **Standard Lot (1.0)**: Large capital. Gold 10-pip move = $100 P&L.
- **Mini Lot (0.1)**: Primary unit for $10k challenge. Dollar P&L ≈ pip movement (50-pip SL ≈ $50 risk).
- **Micro Lot (0.01)**: Minimum entry. For testing or ultra-tight risk.

## Position Sizing Formula
`Lot Size = (Total Risk Amount) / (SL Distance in Pips * Pip Value)`
- Never use fixed lot sizes. Calculate dynamically per trade.
- 1R = the fixed amount risked per trade ($100 on $10k account).

## Entry Rules
- All trades must have SL calculated BEFORE execution.
- Risk must never exceed 1% of $10k balance ($100 per trade).
- Minimum 1:2 RR ratio locked.

## Exit Rules
- TP placed at minimum 1:2 RR from entry.
- Ideal target: 1:3 RR (profitable with only 30% win rate).

## Session Context
- Applies to all sessions and instruments.
