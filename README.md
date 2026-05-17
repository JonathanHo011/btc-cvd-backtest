# BTC CVD + MA20/MA200 Momentum Backtest

## Summary
Backtests a MA20/MA200 golden cross momentum strategy on BTC, with and without a CVD (Cumulative Volume Delta) divergence filter.

Live signal (May 2026): **SHORT** — MA20 below MA200. No bullish crossover detected.

## Key Findings (Aug 2025 – May 2026)

| Strategy    | Return   | Max Drawdown |
|-------------|----------|--------------|
| MA20/MA200  | -13.99%  | -16.36%      |
| MA+CVD      |  -5.51%  |  -5.51%      |
| Buy & Hold   | -31.17%  | -49.53%      |

CVD divergence filter **saved 9.86%** vs MA-only in this period.

> **Note (May 2026):** Earlier versions of this script used a backward-fetch with `endTime` that produced ~500 duplicate klines. This corrupted MA200 values by $12,000+ and generated a **fake bullish crossover on Apr 20, 2026**. This version uses **forward-fetch with deduplication** — always fetch from oldest to newest and dedupe by timestamp before computing indicators.

## Install
```bash
pip install pandas numpy matplotlib requests
```

## Run
```bash
python btc_cvd_backtest.py
```

Output: `btc_cvd_equity_curve.png`

## Data Source
Binance public API — `GET /api/v3/klines?symbol=BTCUSDT&interval=1d&limit=500`

No API key required.