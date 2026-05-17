"""
BTC CVD + MA20/MA200 Momentum Backtest
=======================================
Fetches daily BTCUSDT klines from Binance public API.
Computes MA20/MA200 crossover signals + CVD divergence filter.
Compares equity curves: MA-only vs MA+CVD divergence strategy.

Run: python btc_cvd_backtest.py
Output: btc_cvd_equity_curve.png (saved in same folder)
"""

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta

# ============================================================
# 1. FETCH DAILY KLINES FROM BINANCE
# ============================================================
print("Fetching BTCUSDT daily klines from Binance...")

all_klines = []
batch = 500
end_time = None

while len(all_klines) < 1000:
    params = {"symbol": "BTCUSDT", "interval": "1d", "limit": batch}
    if end_time:
        params["endTime"] = end_time

    r = requests.get("https://api.binance.com/api/v3/klines", params=params)
    r.raise_for_status()
    batch_data = r.json()

    if not batch_data:
        break

    all_klines.extend(batch_data)

    # Stop if we've gone far enough back (2023-01-01)
    oldest_ts = int(batch_data[-1][0])
    if oldest_ts < 1704067200000:  # Jan 1, 2024 in ms
        break

    end_time = oldest_ts - 1

# Keep only the most recent 1000 (Binance max)
all_klines = all_klines[-1000:]

# Parse into DataFrame
df = pd.DataFrame(all_klines, columns=[
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_base", "taker_buy_quote", "ignore"
])

df["dt"] = pd.to_datetime(df["open_time"], unit="ms")
df["close"] = df["close"].astype(float)
df["taker_buy_base"] = df["taker_buy_base"].astype(float)
df["volume"] = df["volume"].astype(float)

df = df.reset_index(drop=True)

# ============================================================
# 2. COMPUTE INDICATORS
# ============================================================
print(f"Data loaded: {df['dt'].iloc[0].strftime('%Y-%m-%d')} → {df['dt'].iloc[-1].strftime('%Y-%m-%d')} ({len(df)} days)")

# Moving averages
df["ma20"] = df["close"].rolling(window=20).mean()
df["ma200"] = df["close"].rolling(window=200).mean()

# CVD: delta = 2 * taker_buy_base - volume  (net buying per bar)
# CVD = cumulative sum of delta
df["delta"] = 2 * df["taker_buy_base"] - df["volume"]
df["cvd"] = df["delta"].cumsum()

# CVD 30-day rolling MA (smoothed baseline)
df["cvd_ma30"] = df["cvd"].rolling(window=30).mean()

# 90-day high/low for CVD divergence detection
df["cvd_90d_high"] = df["cvd"].rolling(window=90).max()
df["cvd_90d_low"] = df["cvd"].rolling(window=90).min()

# ============================================================
# 3. DEFINE SIGNALS
# ============================================================
# MA crossover: 1 = bullish cross (MA20 crosses above MA200), -1 = bearish cross (MA20 crosses below MA200), 0 = no cross
df["ma_cross"] = 0
for i in range(200, len(df) - 1):
    if pd.notna(df["ma20"].iloc[i]) and pd.notna(df["ma200"].iloc[i]) and pd.notna(df["ma20"].iloc[i-1]):
        if df["ma20"].iloc[i-1] < df["ma200"].iloc[i-1] and df["ma20"].iloc[i] >= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = 1   # BUY signal
        elif df["ma20"].iloc[i-1] > df["ma200"].iloc[i-1] and df["ma20"].iloc[i] <= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = -1  # SELL signal

# CVD divergence: price making new 90d high but CVD not confirm
# => price_90d_high AND cvd NOT at 90d high
df["price_90d_high"] = df["close"].rolling(window=90).max()
df["cvd_diverging"] = (
    (df["close"] >= df["price_90d_high"] * 0.98) &  # price near 90d high (within 2%)
    (df["cvd"] < df["cvd_90d_high"] * 0.95)          # CVD NOT confirming (5% below its 90d high)
)

# ============================================================
# 4. BACKTEST ENGINE
# ============================================================
def run_backtest(df, use_cvd_filter=False, start_date="2024-01-01"):
    """
    MA20/MA200 crossover strategy.
    If use_cvd_filter=True: only SELL exits when MA cross fires AND CVD is diverging.
    (BUY entries always on pure MA cross — CVD filter only for exits.)
    """
    start_idx = df[df["dt"] >= pd.to_datetime(start_date)].index[0]

    in_position = False
    entry_price = 0
    entry_date = None
    trades = []

    # Equity curve: start with $10,000
    equity = 10000.0
    equity_curve = []

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1] if i > 0 else None

        if pd.isna(row["ma20"]) or pd.isna(row["ma200"]):
            equity_curve.append(equity)
            continue

        # === ENTRY: MA20 crosses above MA200, and we're flat ===
        if prev is not None and not in_position:
            ma_crossing_up = (
                prev["ma_cross"] == 0 and row["ma_cross"] == 1
            )
            if ma_crossing_up:
                in_position = True
                entry_price = row["close"]
                entry_date = row["dt"]
                trades.append({
                    "type": "BUY", "date": entry_date,
                    "price": entry_price, "equity": equity
                })

        # === EXIT: MA20 crosses below MA200 ===
        if prev is not None and in_position:
            ma_crossing_down = (
                prev["ma_cross"] == 0 and row["ma_cross"] == -1
            )

            if use_cvd_filter:
                # Enhanced exit: SELL if MA cross fires AND CVD was diverging
                # (check if CVD diverged within the last 5 days before the cross)
                cvd_recent_diverging = df["cvd_diverging"].iloc[max(i-5, start_idx):i].any()
                exit_triggered = ma_crossing_down or cvd_recent_diverging
            else:
                exit_triggered = ma_crossing_down

            if exit_triggered:
                exit_price = row["close"]
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                equity = equity * (1 + pnl_pct / 100)
                trades.append({
                    "type": "SELL", "date": row["dt"],
                    "price": exit_price,
                    "pnl_pct": pnl_pct, "equity": equity
                })
                in_position = False

        equity_curve.append(equity)

    # Close open position at last close
    if in_position:
        last_close = df.iloc[-1]["close"]
        pnl_pct = (last_close - entry_price) / entry_price * 100
        equity = equity * (1 + pnl_pct / 100)
        equity_curve[-1] = equity
        trades.append({
            "type": "CLOSE", "date": df.iloc[-1]["dt"],
            "price": last_close, "pnl_pct": pnl_pct, "equity": equity
        })

    return equity_curve, trades, df["dt"].iloc[start_idx:].reset_index(drop=True)


# ============================================================
# 5. RUN BOTH STRATEGIES
# ============================================================
print("\nRunning MA-only strategy (Aug 2025 → today)...")
equity_ma, trades_ma, dates_ma = run_backtest(df, use_cvd_filter=False, start_date="2025-08-01")

print("Running MA+CVD strategy (Aug 2025 → today)...")
equity_cvd, trades_cvd, _ = run_backtest(df, use_cvd_filter=True, start_date="2025-08-01")

# Buy & hold benchmark
start_idx = df[df["dt"] >= pd.to_datetime("2025-08-01")].index[0]
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

final_equity_ma = equity_ma[-1]
final_equity_cvd = equity_cvd[-1]
final_bh = bh_equity[-1]
start_equity = 10000

print(f"\n=== Performance Summary (Aug 2025 → today) ===")
print(f"  MA-only strategy:  ${final_equity_ma:,.2f} ({((final_equity_ma/start_equity)-1)*100:+.2f}%)")
print(f"  MA+CVD strategy:   ${final_equity_cvd:,.2f} ({((final_equity_cvd/start_equity)-1)*100:+.2f}%)")
print(f"  Buy & Hold:         ${final_bh:,.2f} ({((final_bh/start_equity)-1)*100:+.2f}%)")

if final_equity_cvd != final_equity_ma:
    diff = ((final_equity_cvd - final_equity_ma) / final_equity_ma) * 100
    print(f"\n  CVD filter {'saved' if final_equity_cvd > final_equity_ma else 'cost'} {abs(diff):.2f}% vs MA-only")

# ============================================================
# 7. PLOT EQUITY CURVES
# ============================================================
fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [2, 1]})
fig.suptitle("BTC Momentum: MA-only vs MA+CVD Divergence\n(Aug 2025 – May 2026)", fontsize=14, fontweight="bold")

ax1 = axes[0]
ax1.plot(dates_ma, equity_ma, label="MA20/MA200 Only", color="#2196F3", linewidth=2)
ax1.plot(dates_ma, equity_cvd, label="MA20/MA200 + CVD Filter", color="#FF9800", linewidth=2, alpha=0.85)
ax1.plot(dates_ma, bh_equity, label="Buy & Hold", color="grey", linewidth=1.5, alpha=0.7)
ax1.set_ylabel("Portfolio Value ($)")
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
ax1.legend(loc="upper right")
ax1.set_title("Equity Curve Comparison")
ax1.grid(True, alpha=0.3)
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")

# Trade markers on equity curve
for t in trades_ma:
    color = "green" if t["type"] == "BUY" else "red"
    marker = "^" if t["type"] == "BUY" else "v"
    ax1.axvline(t["date"], color=color, linestyle="--", alpha=0.4, linewidth=0.8)
    ax1.scatter([t["date"]], [t["equity"]], color=color, marker=marker, s=80, zorder=5)

# === Bottom: Price chart with CVD ===
ax2 = axes[1]
ax2_twin = ax2.twinx()

# Price
ax2.plot(df["dt"].iloc[start_idx:], df["close"].iloc[start_idx:], color="black", linewidth=1.5, label="BTC Price")
ax2.plot(df["dt"].iloc[start_idx:], df["ma20"].iloc[start_idx:], color="#2196F3", linewidth=1, label="MA20", alpha=0.8)
ax2.plot(df["dt"].iloc[start_idx:], df["ma200"].iloc[start_idx:], color="red", linewidth=1, label="MA200", alpha=0.8)
ax2.fill_between(df["dt"].iloc[start_idx:], df["ma20"].iloc[start_idx:], df["ma200"].iloc[start_idx:],
                  where=(df["ma20"].iloc[start_idx:] >= df["ma200"].iloc[start_idx:]),
                  color="green", alpha=0.08, label="MA above")
ax2.fill_between(df["dt"].iloc[start_idx:], df["ma20"].iloc[start_idx:], df["ma200"].iloc[start_idx:],
                  where=(df["ma20"].iloc[start_idx:] < df["ma200"].iloc[start_idx:]),
                  color="red", alpha=0.08, label="MA below")

# CVD (normalized to price range for comparison)
cvd_visible = df["cvd"].iloc[start_idx:].values
cvd_min_v = cvd_visible.min()
cvd_max_v = cvd_visible.max()
price_min_v = df["close"].iloc[start_idx:].min()
price_max_v = df["close"].iloc[start_idx:].max()
cvd_scaled = (cvd_visible - cvd_min_v) / (cvd_max_v - cvd_min_v + 1e-9) * (price_max_v - price_min_v) + price_min_v
ax2_twin.plot(df["dt"].iloc[start_idx:], cvd_scaled, color="#FF9800", linewidth=1.2, alpha=0.7, label="CVD (scaled)")

ax2.set_ylabel("BTC Price ($)", color="black")
ax2_twin.set_ylabel("CVD (scaled)", color="#FF9800")
ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
ax2.set_title("Price + MA20/MA200 + CVD (normalized)")
ax2.grid(True, alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

# Combine legends
lines1, labels1 = ax2.get_legend_handles_labels()
lines2, labels2 = ax2_twin.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8, ncol=2)

plt.tight_layout()
plt.savefig("btc_cvd_equity_curve.png", dpi=150, bbox_inches="tight")
print(f"\nChart saved: btc_cvd_equity_curve.png")

# ============================================================
# 8. KEY STATS TABLE
# ============================================================
def max_drawdown(equity_series):
    peak = equity_series[0]
    max_dd = 0
    for e in equity_series:
        if e > peak:
            peak = e
        dd = (e - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    return max_dd

def sharpe_ratio(equity_series, risk_free=0.0):
    returns = np.diff(equity_series) / equity_series[:-1]
    excess = returns - risk_free / 252
    if np.std(excess) == 0:
        return 0
    return np.mean(excess) / np.std(excess) * np.sqrt(252)

print(f"\n=== Risk Metrics ===")
for label, eq in [("MA-only", equity_ma), ("MA+CVD", equity_cvd), ("Buy&Hold", bh_equity)]:
    mdd = max_drawdown(eq)
    total = ((eq[-1]/eq[0])-1)*100
    print(f"  {label:12s}: Total: {total:+6.2f}%  |  Max Drawdown: {mdd:6.2f}%")

print("\nDone.")