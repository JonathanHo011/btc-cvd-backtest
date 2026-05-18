"""
BTC CVD + MA20/MA200 Momentum Backtest (v5 — FIXED)
===================================================
Fixes applied:
  A) Mark-to-market equity while in position (not flat-line)
  B) CVD exit uses 20d ROC instead of 90d high threshold

Run: python btc_cvd_backtest.py
Output: btc_cvd_equity_curve.png
"""

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ============================================================
# 1. FETCH DAILY KLINES FROM BINANCE (forward fill, deduped)
# ============================================================
print("Fetching BTCUSDT daily klines from Binance...")

FETCH_START_MS = 1692662400000  # Aug 1, 2023

all_klines = []
batch = 500
start_time = FETCH_START_MS

while len(all_klines) < 2000:
    params = {"symbol": "BTCUSDT", "interval": "1d", "limit": batch, "startTime": start_time}
    r = requests.get("https://api.binance.com/api/v3/klines", params=params)
    r.raise_for_status()
    batch_data = r.json()
    if not batch_data:
        break
    all_klines.extend(batch_data)
    last_ts = int(batch_data[-1][0])
    start_time = last_ts + 86400000
    if start_time > 1767225600000:
        break

# Deduplicate BEFORE DataFrame
seen = set()
deduped = []
for k in all_klines:
    if k[0] not in seen:
        seen.add(k[0])
        deduped.append(k)

print(f"Fetched {len(all_klines)} klines, {len(deduped)} unique after dedup")

df = pd.DataFrame(deduped, columns=[
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_base", "taker_buy_quote", "ignore"
])

df["dt"] = pd.to_datetime(df["open_time"], unit="ms")
df["close"] = df["close"].astype(float)
df["taker_buy_base"] = df["taker_buy_base"].astype(float)
df["volume"] = df["volume"].astype(float)
df = df.sort_values("dt").reset_index(drop=True)

# ============================================================
# 2. COMPUTE INDICATORS
# ============================================================
print(f"Data loaded: {df['dt'].iloc[0].strftime('%Y-%m-%d')} -> {df['dt'].iloc[-1].strftime('%Y-%m-%d')} ({len(df)} days)")

df["ma20"] = df["close"].rolling(window=20).mean()
df["ma200"] = df["close"].rolling(window=200).mean()

first_valid_ma200 = df[df["ma200"].notna()]["dt"].iloc[0]
print(f"First valid MA200: {first_valid_ma200.strftime('%Y-%m-%d')}")

# CVD — cumulative volume delta
df["delta"] = 2 * df["taker_buy_base"] - df["volume"]
df["cvd"] = df["delta"].cumsum()

# --- FIX B: CVD divergence uses 20d rate-of-change instead of 90d high ---
# 90d high threshold was too slow; BTC CVD can drift down for months in bull markets
# 20d ROC catches sudden institutional distribution shifts more responsively
df["cvd_ma20"] = df["cvd"].rolling(20).mean()
df["cvd_std20"] = df["cvd"].rolling(20).std()
df["cvd_zscore"] = (df["cvd"] - df["cvd_ma20"]) / df["cvd_std20"]

# CVD divergence: CVD rolling down while price making 90d highs
# (price at 90d high AND CVD z-score < -1.5 = sharp CVD drop despite price strength)
df["price_90d_high"] = df["close"].rolling(window=90).max()
df["cvd_diverging"] = (
    (df["close"] >= df["price_90d_high"] * 0.97) &
    (df["cvd_zscore"] < -1.5)
)

# ============================================================
# 3. DEFINE MA CROSSOVER SIGNALS
# ============================================================
df["ma_cross"] = 0
for i in range(200, len(df) - 1):
    if (pd.notna(df["ma20"].iloc[i]) and pd.notna(df["ma200"].iloc[i])
            and pd.notna(df["ma20"].iloc[i-1])):
        if df["ma20"].iloc[i-1] < df["ma200"].iloc[i-1] and df["ma20"].iloc[i] >= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = 1
        elif df["ma20"].iloc[i-1] > df["ma200"].iloc[i-1] and df["ma20"].iloc[i] <= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = -1

# ============================================================
# 4. BACKTEST ENGINE (FIX A: mark-to-market open positions)
# ============================================================
BACKTEST_START = "2024-01-01"

def run_backtest(df, use_cvd_filter=False, start_date=BACKTEST_START):
    start_idx = df[df["dt"] >= pd.to_datetime(start_date)].index[0]
    in_position = False
    entry_price = 0.0
    shares_held = 0.0   # qty of BTC held
    trades = []
    equity = 10000.0
    equity_curve = []

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1] if i > 0 else None

        if pd.isna(row["ma20"]) or pd.isna(row["ma200"]):
            equity_curve.append(equity)
            continue

        # ---- ENTRY: MA golden cross ----
        if prev is not None and not in_position:
            ma_crossing_up = prev["ma_cross"] == 0 and row["ma_cross"] == 1
            if ma_crossing_up:
                in_position = True
                entry_price = row["close"]
                shares_held = equity / entry_price   # track qty, not just cash-equiv
                trades.append({
                    "type": "BUY", "date": row["dt"],
                    "price": entry_price, "equity": equity
                })

        # ---- EXIT: MA death cross OR CVD divergence ----
        elif prev is not None and in_position:
            ma_crossing_down = prev["ma_cross"] == 0 and row["ma_cross"] == -1

            if use_cvd_filter:
                # FIX B: use z-score lookback instead of flat 90d high
                # Only fire if CVD z-score has dropped sharply in last 10 days
                cvd_z_recent = df["cvd_zscore"].iloc[max(i-10, start_idx):i]
                cvd_diverging = cvd_z_recent.min() < -1.5 if len(cvd_z_recent) > 0 else False
                exit_triggered = ma_crossing_down or cvd_diverging
            else:
                exit_triggered = ma_crossing_down

            if exit_triggered:
                exit_price = row["close"]
                equity = shares_held * exit_price   # realize at exit price
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                trades.append({
                    "type": "SELL", "date": row["dt"],
                    "price": exit_price,
                    "pnl_pct": pnl_pct, "equity": equity
                })
                in_position = False
                shares_held = 0.0

        # ---- FIX A: mark-to-market equity EVERY day while in position ----
        if in_position:
            # open PnL counted — equity moves with price in real time
            equity_curve.append(shares_held * row["close"])
        else:
            equity_curve.append(equity)

    # Close open position at last available price
    if in_position:
        last_close = df.iloc[-1]["close"]
        equity = shares_held * last_close
        pnl_pct = (last_close - entry_price) / entry_price * 100
        equity_curve[-1] = equity
        trades.append({
            "type": "CLOSE", "date": df.iloc[-1]["dt"],
            "price": last_close, "pnl_pct": pnl_pct, "equity": equity
        })

    dates_out = df["dt"].iloc[start_idx:].reset_index(drop=True)
    return equity_curve, trades, dates_out

# ============================================================
# 5. RUN BOTH STRATEGIES
# ============================================================
start_idx = df[df["dt"] >= pd.to_datetime(BACKTEST_START)].index[0]

print(f"\nRunning strategies ({BACKTEST_START} -> today)...")
equity_ma, trades_ma, dates_ma = run_backtest(df, use_cvd_filter=False, start_date=BACKTEST_START)
equity_cvd, trades_cvd, _ = run_backtest(df, use_cvd_filter=True, start_date=BACKTEST_START)

bh_start_price = df.iloc[start_idx]["close"]
bh_prices = df.iloc[start_idx:]["close"].values
bh_equity = 10000 * (bh_prices / bh_start_price)

# ============================================================
# 6. PRINT RESULTS
# ============================================================
def print_trades(trades, label):
    print(f"\n=== {label} Trades ===")
    for t in trades:
        if t["type"] == "BUY":
            print(f"  BUY  {t['date'].strftime('%Y-%m-%d')} @ ${t['price']:,.2f}")
        elif t["type"] in ("SELL", "CLOSE"):
            print(f"  {t['type']:5} {t['date'].strftime('%Y-%m-%d')} @ ${t['price']:,.2f} | PnL: {t['pnl_pct']:+.2f}%")

print_trades(trades_ma, "MA-only")
print_trades(trades_cvd, "MA+CVD")

final_equity_ma  = equity_ma[-1]
final_equity_cvd = equity_cvd[-1]
final_bh         = bh_equity[-1]
start_equity      = 10000.0

print(f"\n=== Performance Summary ({BACKTEST_START} -> today) ===")
print(f"  MA-only strategy:  ${final_equity_ma:,.2f} ({((final_equity_ma/start_equity)-1)*100:+.2f}%)")
print(f"  MA+CVD strategy:   ${final_equity_cvd:,.2f} ({((final_equity_cvd/start_equity)-1)*100:+.2f}%)")
print(f"  Buy & Hold:        ${final_bh:,.2f} ({((final_bh/start_equity)-1)*100:+.2f}%)")
print(f"\n  CVD filter effect: {((final_equity_cvd/final_equity_ma)-1)*100:+.2f}% vs MA-only")

# ============================================================
# 7. RISK METRICS
# ============================================================
def max_drawdown(equity_series):
    peak = np.maximum.accumulate(equity_series)
    dd = (equity_series - peak) / peak
    return dd.min() * 100

def sharpe(equity_series, risk_free=0.0):
    rets = np.diff(equity_series) / equity_series[:-1]
    ann_ret = rets.mean() * 365
    ann_vol = rets.std() * np.sqrt(365)
    return (ann_ret - risk_free) / ann_vol if ann_vol > 0 else 0.0

print(f"\n=== Risk Metrics ===")
for label, eq in [("MA-only", equity_ma), ("MA+CVD", equity_cvd), ("Buy&Hold", bh_equity)]:
    total_ret = (eq[-1] / start_equity - 1) * 100
    mdd = max_drawdown(np.array(eq))
    shr = sharpe(np.array(eq))
    print(f"  {label:10s}: Total: {total_ret:+7.2f}%  |  Max Drawdown: {mdd:7.2f}%  |  Sharpe: {shr:+.3f}")

# ============================================================
# 8. PLOT 2-PANEL: Equity curves + Price/MAs/CVD
# ============================================================
plt.close("all")
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), sharex=True,
                                gridspec_kw={"height_ratios": [2, 2]})

# Slice to backtest window
start_dt = df["dt"].iloc[start_idx]
end_dt   = df["dt"].iloc[-1]
dt_slice = df["dt"].iloc[start_idx:].reset_index(drop=True)
ma20_slice  = df["ma20"].iloc[start_idx:].values
ma200_slice = df["ma200"].iloc[start_idx:].values
price_slice = df["close"].iloc[start_idx:].values
cvd_slice   = df["cvd_zscore"].iloc[start_idx:].values

# --- Top panel: equity curves ---
ax1.plot(dates_ma.values, equity_ma,  label="MA-only",        color="#2196F3", linewidth=1.8)
ax1.plot(dates_ma.values, equity_cvd, label="MA+CVD Filter", color="#FF9800", linewidth=1.8, alpha=0.9)
ax1.plot(dates_ma.values, bh_equity,  label="Buy & Hold",    color="grey",    linewidth=1.5, alpha=0.7)
ax1.set_ylabel("Portfolio Value ($)", fontsize=11)
ax1.set_title("BTC MA20/MA200 vs MA+CVD Strategy — Equity Curve (2024-01-01 → today)", fontsize=13)
ax1.legend(loc="upper left")
ax1.grid(alpha=0.3)
ax1.set_xlim(dt_slice.iloc[0], dt_slice.iloc[-1])

# --- Bottom panel: price + MAs + CVD z-score on twin axis ---
ax2.plot(dt_slice, price_slice,        color="black",  linewidth=1.2, label="BTC Close")
ax2.plot(dt_slice, ma20_slice,         color="#2196F3", linewidth=0.9, label="MA20",  alpha=0.8)
ax2.plot(dt_slice, ma200_slice,        color="red",    linewidth=0.9, label="MA200", alpha=0.8)
ax2.fill_between(dt_slice.values, ma20_slice, ma200_slice,
                 where=(ma20_slice >= ma200_slice),
                 color="green", alpha=0.12, label="MA above")
ax2.fill_between(dt_slice.values, ma20_slice, ma200_slice,
                 where=(ma20_slice < ma200_slice),
                 color="red", alpha=0.12, label="MA below")

ax2_cvd = ax2.twinx()
ax2_cvd.plot(dt_slice, cvd_slice, color="#FF9800", linewidth=1.2, label="CVD Z-Score", alpha=0.85)
ax2_cvd.fill_between(dt_slice.values, cvd_slice, -1.5,
                     where=(cvd_slice < -1.5),
                     color="#FF9800", alpha=0.15, label="CVD Divergence Zone")
ax2_cvd.set_ylabel("CVD Z-Score", color="#FF9800", fontsize=10)
ax2_cvd.tick_params(axis="y", labelcolor="#FF9800")
ax2_cvd.axhline(-1.5, color="#FF9800", linewidth=0.8, linestyle="--", alpha=0.5)
ax2_cvd.axhline(0,    color="grey",   linewidth=0.6, linestyle="-",  alpha=0.4)

# Merge legends
lines1, labels1 = ax2.get_legend_handles_labels()
lines2, labels2 = ax2_cvd.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=9)
ax2.set_xlabel("Date")
ax2.grid(alpha=0.3)
ax2.set_xlim(dt_slice.iloc[0], dt_slice.iloc[-1])

# Trade markers on price chart
for t in trades_ma:
    if t["type"] == "BUY":
        ax2.axvline(t["date"], color="green", linestyle=":", alpha=0.5, linewidth=0.8)
    elif t["type"] in ("SELL", "CLOSE"):
        ax2.axvline(t["date"], color="red", linestyle=":", alpha=0.5, linewidth=0.8)

plt.tight_layout()
plt.savefig("btc_cvd_equity_curve.png", dpi=120, bbox_inches="tight")
print(f"\nChart saved: btc_cvd_equity_curve.png")
print("Done.")