# BTC MA20/MA200 Momentum Backtest — Ongoing Research Project

## Overview

Backtests a MA20/MA200 golden/death cross strategy on BTC/USDT (Binance daily candles), iteratively improving it through multiple versions. Each version builds on the last, with bugs documented, hypotheses tested, and failures recorded alongside wins.

**Period:** Jan 2024 → May 2026  
**Starting capital:** $10,000  
**Data:** Binance public API — no API key required

---

## Current Best Result (v6 — May 22, 2026)

### Strategy: MA Golden Cross Entry + 10% Trailing Stop Exit

| Metric | MA-only (v5 baseline) | MA + 10% Trail (v6) | Improvement |
|--------|----------------------|---------------------|-------------|
| **Total Return** | +28.35% | **+58.09%** | **+29.74%** |
| **Max Drawdown** | -25.95% | **-12.73%** | **-13.22%** |
| **Sharpe Ratio** | +0.522 | **+1.118** | First time above 1.0 |
| **vs Buy & Hold** | -46.97% | -17.23% | +29.74% relative |

### Trade Log (v6 — 10% Trail)

| Date | Action | Price | PnL | Exit Reason |
|------|--------|-------|-----|-------------|
| 2024-10-18 | BUY | $68,428 | — | Golden cross |
| 2024-12-22 | SELL | $95,186 | **+39.10%** | Trailing stop |
| 2025-05-02 | BUY | $96,887 | — | Golden cross |
| 2025-08-25 | SELL | $110,112 | **+13.65%** | Trailing stop |

> **Both exits were trailing stop exits** — the MA death cross never had a chance to fire. The stop caught the exits 2-3 months earlier than waiting for the death cross, capturing significantly more profit.

### Trade Log (v5 — MA-only, for comparison)

| Date | Action | Price | PnL | Exit Reason |
|------|--------|-------|-----|-------------|
| 2024-10-18 | BUY | $68,428 | — | Golden cross |
| 2025-03-22 | SELL | $83,841 | **+22.52%** | Death cross |
| 2025-05-02 | BUY | $96,887 | — | Golden cross |
| 2025-11-04 | SELL | $101,497 | **+4.76%** | Death cross |

> The MA death cross sold $83.8K vs the trailing stop's $95.2K (Trade 1) — the trailing stop captured an extra **+16.6%** by exiting while the trend was still intact. Trade 2: $110.1K vs $101.5K — extra **+8.9%**.

---

## Version History

| Version | Date | Key Change | Return | MaxDD | Sharpe |
|---------|------|-----------|--------|-------|--------|
| **v6** | May 22, 2026 | **10% trailing stop exit** | **+58.09%** | **-12.73%** | **+1.118** |
| v5 | May 18, 2026 | Mark-to-market fix; CVD z-score | +28.35% | -25.95% | +0.522 |
| v4 | May 17, 2026 | Forward fetch + dedup; CVD divergence | Broken (data corruption) | — | — |
| v3 | May 14, 2026 | Original CVD filter (90d high) | +0.15% (CVD filter destroyed returns) | — | — |
| v2 | May 11, 2026 | Walk-forward validation | +2.38% | -35.32% | -0.036 |
| v1 | May 9, 2026 | Initial MA20/MA200 backtest | +0.55% | -35.32% | -0.036 |

### Key Lessons by Version

**v6 (Trailing Stop):** Adding a 10% trailing stop transformed the strategy. Both exits fired on the stop, not the MA death cross — the MA crossover is too slow as an exit signal. 10% was optimal (tight enough to protect gains, wide enough not to whipsaw). The trailing stop is an exit-only improvement — entries remain purely MA golden cross.

**v5 (Mark-to-Market Fix):** Fixed a critical bug that made the equity curve flat during open positions. MaxDD went from 0% (meaningless) to -25.95% (real). This was the session where CVD was **rejected** as an exit filter — it destroyed +28.2% of returns.

**v4 (Data Fix):** Binance backward fetch produced 500 duplicate klines. MA200 was off by $12,000. A fake BUY crossover appeared on Apr 20, 2026 that did not exist in the data.

**v3 (CVD Failure):** The CVD 90d high threshold was permanently triggered because BTC spot CVD was structurally declining during the 2024-2025 bull run. Instant exit after every BUY. This was the session where we discovered **why** spot CVD fails as a daily BTC filter.

**v2 (Walk-Forward):** Confirmed the MA crossover signal is real (train +5.03% vs test +4.58%, gap ~0.45%), but absolute performance is regime-dependent and poor in bull markets.

**v1 (Initial):** First working backtest. Strategy barely positive while BTC was +30%.

---

## Bugs Discovered & Fixed

### Bug 1: Flat Equity Line During Open Positions (v5)
**Symptom:** Equity curve was flat during every trade — MaxDD reported as 0%.  
**Root cause:** Equity only updated on trade close, not while position was open.  
**Fix:** Track `shares_held = entry_equity / entry_price` and mark-to-market daily.

### Bug 2: Backward Fetch Duplicates (v4)
**Symptom:** MA200 values off by $12,000; fake BUY crossover generated.  
**Root cause:** Binance backward pagination (`endTime`) produces ~50% duplicate klines.  
**Fix:** Forward fetch + deduplicate by timestamp before DataFrame creation.

### Bug 3: CVD 90d High Threshold Permanently Triggered (v3)
**Symptom:** CVD filter caused instant exit after every BUY.  
**Root cause:** BTC spot CVD in structural decline during the bull run — 90d rolling high always rolling forward.  
**Fix:** Replaced with 20d z-score (v5) — then abandoned CVD entirely for daily spot BTC.

---

## Strategy Logic (Current — v6)

```
Entry:  MA20 crosses ABOVE MA200 → BUY (golden cross)
Exit:   Close drops 10% below highest close since entry → SELL (trailing stop)
         — OR —
        MA20 crosses BELOW MA200 → SELL (death cross, fallback only)

The trailing stop ratchets UP only — never down.
If price rises, the stop follows. If price falls, the stop stays.
```

No transaction costs modeled. No stop-loss (the trailing stop handles risk management).

---

## CVD Experiment — Why It Failed & What We Learned

The Cumulative Volume Delta (CVD) filter was extensively tested across v3-v5 and ultimately **rejected** for daily BTC spot data. This is documented transparently because knowing what *doesn't* work is as valuable as knowing what does.

**What we tried:**
- 90d CVD high divergence (v3) → permanently triggered, destroyed all returns
- 20d CVD z-score divergence (v5) → cost +28.2% vs MA-only, blocked re-entries during rallies

**Why spot CVD fails structurally on daily BTC:**
1. **Stablecoin pair rotations** → capital rotating into USDT/USDC looks identical to distribution
2. **Perpetuals absorb volume** → spot volume dries up while price rises; CVD diverges with no directional signal
3. **Market maker delta-neutral arb** → cross-exchange arbitrage creates synthetic sell pressure without conviction

**Future CVD work:** If revisiting, try (a) perp CVD from futures data instead of spot, (b) cross-exchange comparison (Binance vs Coinbase spot CVD), or (c) higher-frequency data (4H candles).

---

## Files

| File | Description |
|------|-------------|
| `btc_trailing_stop_backtest.py` | **Current (v6)** — MA golden cross + trailing stop variants |
| `btc_cvd_backtest.py` | v5 — MA crossover + CVD filter (historical reference) |
| `btc_trailing_stop_equity_curve.png` | 3-panel chart: equity comparison, drawdown, price + trades |
| `btc_cvd_equity_curve.png` | v5 chart (historical) |
| `README.md` | This file |

---

## How to Run

```bash
# Install dependencies
pip install pandas numpy matplotlib requests

# Run v6 (trailing stop)
python btc_trailing_stop_backtest.py
```

---

## Next Steps

| Priority | Improvement | Rationale |
|----------|-------------|-----------|
| 1 | **CVD entry filter** | Test CVD as entry gating (skip fake golden crosses) — different role than exit filter |
| 2 | **Perp CVD + funding rates** | Futures data may avoid spot CVD's structural noise |
| 3 | **Multi-asset test** | Test trailing stop on ETH, SOL, gold — does it generalize? |
| 4 | **Transaction costs** | Model 0.1-0.5% per trade for realistic execution |

---

## Limitations

- No transaction costs or slippage (real execution ~0.1-0.5% per trade)
- Only 2 trades in v6 — small sample, cannot conclude on long-term edge
- Backtest window ~2.3 years — insufficient for statistical significance
- Trailing stop % optimized in-sample — may not generalize
- Past performance ≠ future results

---

## Live Signal (May 22, 2026)

- BTC: ~$77,457
- MA20: below MA200 (death cross active since Nov 4, 2025)
- Position: **Flat**
- ATH: $124,659 (Oct 6, 2025)
- Next watch: Monitoring for golden cross

---

*This is an ongoing research project — versions are deliberately incremental. Failures are documented. Improvements are quantitative. The goal is building a credible portfolio of trading research for crypto quant roles.*
