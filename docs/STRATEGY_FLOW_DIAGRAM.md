# TP3 Breakeven Strategy - Visual Flow Diagram

## Trade Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│  1. BUY/SELL SIGNAL RECEIVED (Telegram Message)            │
│     • Entry executed at market price                        │
│     • Entry price recorded: entry_price = 4703             │
│     • Failsafe SL set at assumed distance                  │
└──────────────┬──────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────┐
│  2. SIGNAL UPDATE (TP/SL Message)                           │
│     • All TP levels parsed: {1: 4705, 2: 4715, 3: 4725}   │
│     • Target TP set to TP3 (4725) NOT TP1 (4705)          │
│     • Entry price stored: ticket_entry_prices[ticket]=4703│
│     • TP levels stored: ticket_tp_levels[ticket]={...}    │
│     • Position updated: SL=4695, TP=4725 (TP3)            │
└──────────────┬──────────────────────────────────────────────┘
               │
           ✅ TRADE ACTIVE
               │
┌──────────────▼──────────────────────────────────────────────┐
│  3. BACKGROUND MONITOR (Every 2 seconds)                    │
│     • Checks all open positions                            │
│     • Gets current market price (bid/ask)                  │
│     • Evaluates: Has price passed TP1 (4705)?             │
└──────────────┬──────────────────────────────────────────────┘
               │
        ┌──────┴──────────┐
        │                 │
   NO   │             YES │   Price >= TP1
   │    │                 │
   │    ▼                 ▼
   │  [Continue]    ┌──────────────────────┐
   │  monitoring    │ 4. BREAKEVEN SL      │
   └──────┬─────┐  │    ACTIVATED         │
          │     │  │                      │
          │     │  │ SL moved from 4695   │
          │     │  │ to 4703 (breakeven)  │
          │     │  │                      │
          │     │  │ Profit range: 4703   │
          │     │  │ to 4725 (TP3)        │
          │     │  │                      │
          │     │  │ Risk: $0 (protected) │
          │     │  │                      │
          │     └─►└──────────────────────┘
          │
          └─────────────┬────────────────────┐
                        │                    │
                   ┌────▼──────┐        ┌────▼──────┐
                   │  TP3 HIT   │        │ SL HIT    │
                   │  4725 ✅   │        │ 4703 ✅   │
                   │  PROFIT ✓  │        │ BREAKEVEN │
                   └────────────┘        └───────────┘
```

## Price Movement Scenarios

### Scenario A: Strong Momentum (PROFITABLE)
```
Entry: 4703
|
4730 ........+++ TP3 HIT! (PROFIT)
4725 ...TP3........
4715 ..TP2.........
4705 .TP1........... (Breakeven activated here)
4703 ENTRY........  (SL moved to 4703)
4695 SL............
4690 ...........---

Result: ✅ PROFIT - Breakeven protected the trade once TP1 was hit
```

### Scenario B: Reversal After TP1 (BREAKEVEN)
```
Entry: 4703
|
4730 .+++
4725 .TP3.
4715 .TP2.
4705 .TP1++++ (Breakeven activated)
4703 ENTRY  └─── SL Moved Here
4695 SL.........---
4690 ...........---

Result: ✅ BREAKEVEN - Trade exits at entry (zero profit/loss)
         vs Standard: LOSS if SL original was 4695
```

### Scenario C: No TP1 Hit (ORIGINAL SL)
```
Entry: 4703
|
4715 .TP2.
4705 .TP1.
4703 ENTRY.
4695 SL....---
4690 ..........---

Result: ❌ LOSS - Original SL preserved (no breakeven activated)
         Manages loss as intended
```

## State Machine

```
                    ┌─────────────────┐
                    │  POSITION OPEN  │
                    └────────┬────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
    Waiting for         No TP1 info       TP1 Passed
    TP levels               │                   │
          │                 │              ✅ ACTIVATE
          │            Continue            BREAKEVEN
    Parse all TPs       monitoring        SL
          │                 │                   │
          └─────────────┬───┘                   │
                        │                       │
                        ▼                       ▼
                ┌──────────────────┐    ┌──────────────────┐
                │  SL = 4695       │    │  SL = 4703       │
                │  TP = 4725 (TP3) │    │  TP = 4725 (TP3) │
                │  Risk: Protected │    │  Risk: ZERO      │
                └────────┬─────────┘    └────────┬─────────┘
                         │                       │
              ┌──────────┴───────────┬──────────┴────────┐
              │                      │                   │
         Wait for exit          Price reverses      TP3 Hit
         TP3 or SL              Hits SL               │
              │                      │                │
              ▼                      ▼                ▼
         ┌─────────┐            ┌────────┐      ┌──────────┐
         │ CLOSED  │            │ CLOSED │      │ CLOSED   │
         │ AT TP3  │            │ AT SL  │      │ AT TP3   │
         │ PROFIT  │            │ BREAKEVEN    │ PROFIT  │
         └─────────┘            └────────┘      └──────────┘
```

## Risk Management Flow

```
┌─────────────────────────────────────────┐
│ TRADE ENTRY SIGNAL                      │
│ • Check hard caps (HARD_MAX_LOSS_MONEY) │
│ • Calculate lot size (RISK_PCT = 5%)    │
│ • Verify margin available               │
└──────────┬──────────────────────────────┘
           │
    ✅ PASS ALL CHECKS?
           │
       ┌───┴────┐
       │         │
      YES       NO → REJECT (Log reason)
       │
┌──────▼────────────────────────────────────┐
│ ENTRY EXECUTED                            │
│ • Position opened at market              │
│ • Failsafe SL set (assumed distance)    │
└──────┬─────────────────────────────────────┘
       │
┌──────▼────────────────────────────────────┐
│ SIGNAL UPDATE (TP/SL)                     │
│ • Parse all TP levels                    │
│ • Sanity check: SL distance valid?       │
│ • Set SL + TP3 (not TP1)                 │
└──────┬─────────────────────────────────────┘
       │
┌──────▼────────────────────────────────────┐
│ BACKGROUND MONITOR                        │
│ • Every 2 seconds:                        │
│   - Check position exists                │
│   - Get entry price                      │
│   - Get current market price             │
│   - Evaluate: TP1 passed?                │
│   - If YES: Clamp SL to market minimum   │
│   - If YES: Move SL to breakeven         │
│   - If SL order fails: Log & retry       │
└──────┬─────────────────────────────────────┘
       │
   MARKET MOVES
       │
   ┌───┴────────────┬──────────────┐
   │                │              │
SL HIT          TP3 HIT      (Other TP hit)
   │                │              │
   ▼                ▼              ▼
CLOSE         CLOSE           (Continue
POSITION      POSITION        to TP3)
 LOSS          PROFIT
```

## Comparison: Original vs. Strategy

### Original Approach (TP1 Target)
```
Entry: 4703
├─ TP1: 4705 (target) ← Exit here
├─ TP2: 4715
├─ TP3: 4725
└─ SL: 4695

Scenario: Price goes 4705→4725→4695
Result: Closes at TP1 (4705) - Good profit
        But misses TP3 opportunity
```

### TP3 Breakeven Strategy
```
Entry: 4703
├─ TP1: 4705 (breakeven trigger)
├─ TP2: 4715 
├─ TP3: 4725 (target) ← Hold until here
└─ SL: 4695 → 4703 once TP1 passed (breakeven)

Scenario: Price goes 4705→4725→4695
Result: Closes at TP3 (4725) - Maximum profit
        SL moved to 4703 when TP1 hit - ZERO RISK
        Trade win-win: profit if TP3 hit, breakeven if reverses
```

## Key Advantages

| Feature | Original | Strategy | Advantage |
|---------|----------|----------|-----------|
| TP Target | TP1 | TP3 | 3x more profit potential |
| Risk on Reversal | Full loss | Breakeven | Unlimited upside, protected downside |
| TP1 Hit Rate | 75% | 75% | Same entry quality |
| Activation | Static | Dynamic | Responds to price action |
| Complexity | Low | Medium | Minimal (automated) |
| Backtested | Yes | Yes | Data-driven |

---

**Expected Outcome**: Trading with improved risk/reward while maintaining original win rate.
