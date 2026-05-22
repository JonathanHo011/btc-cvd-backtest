# BTC MA20/MA200 Momentum Backtest — Ongoing Research Project

## Overview

Backtests a MA20/MA200 golden/death cross strategy on BTC/USDT (Binance daily candles), iteratively improving it through multiple versions. Each version builds on the last, with bugs documented, hypotheses tested, and failures recorded alongside wins.

**Period:** Jan 2024 → May 2026  
**Starting capital:** $10,000  
**Data:** Binance public API — no API key required

---

## Current Best Result (v6/v7 — May 22, 2026)

### Strategy: MA Golden Cross Entry + 10% Trailing Stop Exit

| Metric | MA-only (v5 baseline) | MA + 10% Trail (v6) | Improvement |
|--------|----------------------|---------------------|-------------|
| **Total Return** | +28.35% | **+58.09%** | **+29.74%** |
| **Max Drawdown** | -25.95% | **-12.73%** | **-13.22%** |
| **Sharpe Ratio** | +0.522 | **+1.118** | First time above 1.0 |
| **vs Buy & Hold** | -46.97% | -17.23% | +29.74% relative |

### Trade Log (v6/v7 — 10% Trail, No CVD Filter)

| Date | Action | Price | PnL | Exit Reason |
|------|--------|-------|-----|-------------|
| 2024-10-18 | BUY | $68,428 | — | Golden cross |
| 2024-12-22 | SELL | $95,186 | **+39.10%** | Trailing stop |
| 2025-05-02 | BUY | $96,887 | — | Golden cross |
| 2025-08-25 | SELL | $110,112 | **+13.65%** | Trailing stop |

> Both exits were trailing stop exits — the MA death cross never had a chance to fire. The stop caught the exits 2-3 months earlier than waiting for the death cross.

### Trade Log (v5 — MA-only, for comparison)

| Date | Action | Price | PnL | Exit Reason |
|------|--------|-------|-----|-------------|
| 2024-10-18 | BUY | $68,428 | — | Golden cross |
| 2025-03-22 | SELL | $83,841 | **+22.52%** | Death cross |
| 2025-05-02 | BUY | $96,887 | — | Golden cross |
| 2025-11-04 | SELL | $101,497 | **+4.76%** | Death cross |

---

## Version History

| Version | Date | Key Change | Return | MaxDD | Sharpe | Verdict |
|---------|------|-----------|--------|-------|--------|---------|
| **v6** | May 22, 2026 | **10% trailing stop exit** | **+58.09%** | **-12.73%** | **+1.118** | ✅ **CURRENT BEST** |
| v7 | May 22, 2026 | CVD entry filter tested | +58.09% | -12.73% | +1.118 | ❌ CVD blocked ALL entries |
| v5 | May 18, 2026 | Mark-to-market fix; CVD z-score exit | +28.35% | -25.95% | +0.522 | ✅ MA-only works, CVD exit rejected |
| v4 | May 17, 2026 | Forward fetch + dedup; CVD divergence | Broken (data corruption) | — | — | 🐛 Bug discovery |
| v3 | May 14, 2026 | Original CVD filter (90d high) | +0.15% (CVD destroyed returns) | — | — | ❌ CVD exit rejected |
| v2 | May 11, 2026 | Walk-forward validation | +2.38% | -35.32% | -0.036 | ✅ Signal is real |
| v1 | May 9, 2026 | Initial MA20/MA200 backtest | +0.55% | -35.32% | -0.036 | 🏁 Baseline |

---

## CVD Experiment — Complete Postmortem (v3, v5, v7)

CVD (Cumulative Volume Delta) was tested as both an **exit** filter and an **entry** filter. Both failed decisively on daily BTC spot data.

### CVD as Exit Filter (v3, v5) → ❌ REJECTED
- v3: 90d high threshold permanently triggered — instant exit after every BUY
- v5: 20d z-score cost +28.2% vs MA-only — panic-sold during normal consolidation
- **Reason:** BTC spot CVD is structurally noisy on daily candles — stablecoin rotations, perp dominance, and MM delta-neutral arb create false signals

### CVD as Entry Filter (v7) → ❌ REJECTED
- **Z-Score > 0:** blocked BOTH golden crosses → 0 trades, +0.00%
- **Z-Score > -0.5:** blocked BOTH → 0 trades, +0.00%
- **Slope > 0:** blocked BOTH → 0 trades, +0.00%
- **ROC(20d) > 0%:** let both through → same as v6 (no filtering effect)
- **Conclusion:** Any CVD threshold strict enough to filter bad entries also filters good entries — spot CVD has zero discriminating power at golden cross moments

### Why Spot CVD Fails Structurally on Daily BTC
1. **Stablecoin pair rotations** — capital moving into USDT looks identical to distribution
2. **Perpetuals absorb volume** — spot volume dries up while price rises, CVD diverges without signal
3. **Market maker delta-neutral arb** — cross-exchange arbitrage creates synthetic sell pressure without directional conviction

### What's Left for CVD
- Perp CVD (futures data) may avoid spot's structural noise
- Cross-exchange comparison (Binance vs Coinbase) could isolate ETF-driven flow
- Higher frequency data (4H candles) might show cleaner patterns

**Bottom line:** CVD on daily Binance spot BTC is not useful as a trade filter — tested exhaustively across 3 versions with 6+ variants. This is a real, documented finding that belongs in the portfolio.

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
**Fix:** Abandoned 90d method. Replaced with z-score in v5, then abandoned CVD entirely.

---

## Strategy Logic (Current — v6/v7)

```
Entry:  MA20 crosses ABOVE MA200 → BUY (golden cross)
Exit:   Close drops 10% below highest close since entry → SELL (trailing stop)
         — OR —
        MA20 crosses BELOW MA200 → SELL (death cross, fallback)

The trailing stop ratchets UP only — never down.
If price rises, the stop follows. If price falls, the stop stays.
```

No CVD filter. No transaction costs modeled.

---

## Files

| File | Description |
|------|-------------|
| `btc_trailing_stop_backtest.py` | **Current (v6)** — MA golden cross + trailing stop, no CVD |
| `btc_cvd_entry_filter.py` | v7 — CVD entry filter variants (all rejected) |
| `btc_cvd_backtest.py` | v5 — original CVD exit filter (historical) |
| `btc_trailing_stop_equity_curve.png` | v6 chart |
| `btc_cvd_entry_filter_equity.png` | v7 chart |
| `btc_cvd_equity_curve.png` | v5 chart (historical) |
| `README.md` | This file |

---

## How to Run

```bash
pip install pandas numpy matplotlib requests
python btc_trailing_stop_backtest.py
```

---

## Next Steps

| Priority | Improvement | Rationale |
|----------|-------------|-----------|
| 1 | **Perp CVD + funding rates** | Futures data may avoid spot's structural noise — different market |
| 2 | **Multi-asset test** | Does trailing stop generalize to ETH, SOL, gold? |
| 3 | **Transaction costs & slippage** | Model 0.1-0.5% per trade for realistic execution |

---

## Limitations

- No transaction costs or slippage
- Only 2 trades — small sample, cannot conclude on long-term edge
- Backtest window ~2.3 years — insufficient for statistical significance
- Trailing stop % optimized in-sample — may not generalize
- Past performance ≠ future results

---

## Live Signal (May 22, 2026)

- BTC: ~$77,457
- MA20: below MA200 (death cross since Nov 4, 2025)
- Position: **Flat**
- ATH: $124,659 (Oct 6, 2025)
- Next watch: Monitoring for golden cross

---

*This is an ongoing research project — versions are deliberately incremental. Failures are documented. Improvements are quantitative. The goal is building a credible portfolio of trading research for crypto quant roles.*
