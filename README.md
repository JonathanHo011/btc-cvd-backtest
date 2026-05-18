# BTC MA20/MA200 + CVD Momentum Backtest

## Overview

Backtests a MA20/MA200 golden/death cross strategy on BTC/USDT (Binance daily candles), with and without a CVD (Cumulative Volume Delta) divergence filter as an exit override.

**Period:** Jan 2024 → May 2026
**Starting capital:** $10,000
**Data:** Binance public API — no API key required

---

## Results

| Strategy | Final Equity | Total Return | Max Drawdown | vs Buy&Hold |
|----------|-------------|--------------|--------------|-------------|
| **MA-only** | $12,835 | **+28.35%** | **-25.95%** | -46.97% |
| **Buy & Hold** | $17,532 | **+75.32%** | **-49.53%** | — |

> **CVD filter:** Dropped return to **+0.15%** — cost **28.2%** vs MA-only. CVD caused early exits and blocked re-entries during the biggest BTC rallies of the period. See "Why CVD Failed" below.

**Trade Log (MA-only):**

| Date | Action | Price | PnL |
|------|--------|-------|-----|
| 2024-10-18 | BUY | $68,428 | — |
| 2025-03-22 | SELL | $83,841 | **+22.52%** |
| 2025-05-02 | BUY | $96,887 | — |
| 2025-11-04 | SELL | $101,497 | **+4.76%** |

**Currently:** Flat (MA20 below MA200 — death cross on Nov 4 2025).

---

## Key Findings

### 1. MA Crossover Is a Lagging Indicator — Market Timing Is Everything

The strategy BUY fires only after BTC has already been in an uptrend for months. In this backtest:
- BTC bottomed ~$40K in Sep 2024; strategy entered at $68K on Oct 18 2024
- BTC peaked at $124K on Oct 6 2025; strategy never captured that move cleanly

The signal is **real** (validated by walk-forward analysis in prior sessions: train +5.03% vs test +4.58%, gap ~0.45%) but its absolute performance is entirely dependent on primary market direction.

### 2. The CVD Filter Was Counterproductive

**What happened:** CVD divergence was originally intended as an early exit filter to avoid fakeouts. In practice:
- BTC's spot CVD (Binance `taker_buy_base`) is structurally noisy on daily candles
- CVD z-score < -1.5 triggers frequently during normal consolidation — not just institutional distribution
- Every time the MA strategy generated a BUY, the CVD filter panic-sold a few days later
- The filter also blocked re-entries during the Apr–Oct 2025 BTC rally (+46%)

**Why spot CVD fails as a daily filter:**
- Capital rotates to stablecoins during risk-off periods → spot CVD drops even without selling
- Perpetuals/futures markets absorb volume that doesn't appear in spot CVD
- Delta-neutral arbitrage by market makers creates CVD-price divergence without directional signal

### 3. Strategy vs Market Regime

| Period | BTC Direction | MA-only Performance | Notes |
|--------|--------------|---------------------|-------|
| Oct 2024 → Mar 2025 | $68K → $83K (+22%) | +22.52% ✅ | Caught the move |
| Mar 2025 → May 2025 | $83K → $97K (+17%) | Exited too early ❌ | Missed recovery (CVD block) |
| May 2025 → Nov 2025 | $97K → $101K (+5%) | +4.76% ✅ | Flat/chop, rode it |
| Nov 2025 → May 2026 | $101K → $77K (-23%) | Flat | **Missed ATH $124K (+23% from exit)** — flat through the rally to $124K and entire drawdown to $77K |

The strategy worked when BTC moved directionally; it underperformed or got blocked when BTC chopped or recovered.

---

## Bugs Fixed (v4 → v5)

### Bug 1: Flat Equity Line While In Position
**Problem:** Equity only updated on trade close. While in an open position, the equity curve appended the static entry-equity value — making it a flat line during the entire position, not reflecting mark-to-market PnL.

**Fix:** Track `shares_held = entry_equity / entry_price` and mark-to-market every day:
```python
if in_position:
    equity_curve.append(shares_held * row["close"])  # updates every day
else:
    equity_curve.append(equity)
```

### Bug 2: CVD 90d High Threshold Permanently Triggered
**Problem:** Original code: `cvd < cvd_90d_high * 0.95`. BTC's spot CVD was in a structural downtrend during the 2024-2025 bull run — the 90d rolling high kept rolling forward, making the condition almost always true. Result: instant exit after every BUY.

**Fix:** Replaced with 20d CVD z-score — divergence only fires on a sharp, sudden CVD drop (z-score < -1.5) while price is at 90d highs. Too responsive for daily BTC — see findings above.

### Bug 3: Backward Fetch Duplicates (discovered earlier)
**Problem:** Using Binance backward pagination (`endTime`) produces ~500 duplicate klines out of 1000 rows. Rolling indicators calculated on corrupted data gave wrong MA200 values (off by $12,000) and generated a fake BUY crossover on Apr 20 2026.

**Fix:** Forward fetch + deduplicate by timestamp before computing any indicators.

---

## Strategy Logic

```
Entry:  MA20 crosses ABOVE MA200  → BUY (long)
Exit:   MA20 crosses BELOW MA200  → SELL (flat)

With CVD filter: also exits early if CVD z-score < -1.5 while price at 90d high
(Not recommended — see findings above)
```

No transaction costs modeled. No stop-loss. Pure signal-driven.

---

## Files

| File | Description |
|------|-------------|
| `btc_cvd_backtest.py` | Full backtest engine + charting (v5 fixed) |
| `btc_cvd_equity_curve.png` | 2-panel chart: equity curves + price/MAs/CVD |
| `README.md` | This file |

---

## Limitations

- No transaction costs (real execution would cost ~0.1-0.5% per trade)
- No slippage modeling
- No stop-loss or risk management
- CVD data is from Binance spot only — may not capture full institutional flow
- Backtest window ~2.3 years — insufficient to conclude on long-term edge
- Past performance ≠ future results

---

## Conclusion

MA20/MA200 crossover is a **real signal** (walk-forward validated) but is regime-dependent — it significantly underperforms buy-and-hold in sustained bull markets due to lag. The CVD divergence filter was tested and **rejected** for daily BTC spot data — too noisy, structurally misaligned with BTC's market structure. Further research direction: try CVD on higher timeframe (4H, daily with smoothing) or replace with futures data for more accurate institutional flow detection.

---

*Last updated: May 2026 | Data source: Binance public API | Period: Jan 2024 – May 2026*