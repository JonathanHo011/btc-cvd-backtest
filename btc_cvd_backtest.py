"""
BTC CVD + MA20/MA200 Momentum Backtest (v4)
=============================================
Timeline: Feb 2024 (MA200 valid) → today
Backtest window: 2024-01-01 → today
MA200 first valid: ~Feb 2024
CVD data from Aug 2023 to get accurate MA200 lookback.

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
    if start_time > 1767225600000:  # stop well past today
        break

# Deduplicate by timestamp BEFORE creating DataFrame
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

df["delta"] = 2 * df["taker_buy_base"] - df["volume"]
df["cvd"] = df["delta"].cumsum()

df["cvd_90d_high"] = df["cvd"].rolling(window=90).max()
df["cvd_90d_low"] = df["cvd"].rolling(window=90).min()

# ============================================================
# 3. DEFINE SIGNALS
# ============================================================
df["ma_cross"] = 0
for i in range(200, len(df) - 1):
    if (pd.notna(df["ma20"].iloc[i]) and pd.notna(df["ma200"].iloc[i])
            and pd.notna(df["ma20"].iloc[i-1])):
        if df["ma20"].iloc[i-1] < df["ma200"].iloc[i-1] and df["ma20"].iloc[i] >= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = 1
        elif df["ma20"].iloc[i-1] > df["ma200"].iloc[i-1] and df["ma20"].iloc[i] <= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = -1

df["price_90d_high"] = df["close"].rolling(window=90).max()
df["cvd_diverging"] = (
    (df["close"] >= df["price_90d_high"] * 0.98) &
    (df["cvd"] < df["cvd_90d_high"] * 0.95)
)

# ============================================================
# 4. BACKTEST ENGINE
# ============================================================
# Backtest window: Jan 1, 2024 → today
# MA200 is valid from ~Feb 2024, so signals only fire after that
BACKTEST_START = "2024-01-01"

def run_backtest(df, use_cvd_filter=False, start_date=BACKTEST_START):
    start_idx = df[df["dt"] >= pd.to_datetime(start_date)].index[0]
    in_position = False
    entry_price = 0
    trades = []
    equity = 10000.0
    equity_curve = []

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1] if i > 0 else None

        if pd.isna(row["ma20"]) or pd.isna(row["ma200"]):
            equity_curve.append(equity)
            continue

        if prev is not None and not in_position:
            ma_crossing_up = prev["ma_cross"] == 0 and row["ma_cross"] == 1
            if ma_crossing_up:
                in_position = True
                entry_price = row["close"]
                trades.append({
                    "type": "BUY", "date": row["dt"],
                    "price": entry_price, "equity": equity
                })

        if prev is not None and in_position:
            ma_crossing_down = prev["ma_cross"] == 0 and row["ma_cross"] == -1

            if use_cvd_filter:
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

final_equity_ma = equity_ma[-1]
final_equity_cvd = equity_cvd[-1]
final_bh = bh_equity[-1]
start_equity = 10000

print(f"\n=== Performance Summary ({BACKTEST_START} -> today) ===")
print(f"  MA-only strategy:  ${final_equity_ma:,.2f} ({((final_equity_ma/start_equity)-1)*100:+.2f}%)")
print(f"  MA+CVD strategy:   ${final_equity_cvd:,.2f} ({((final_equity_cvd/start_equity)-1)*100:+.2f}%)")
print(f"  Buy & Hold:         ${final_bh:,.2f} ({((final_bh/start_equity)-1)*100:+.2f}%)")

if final_equity_cvd != final_equity_ma:
    diff = ((final_equity_cvd - final_equity_ma) / final_equity_ma) * 100
    print(f"\n  CVD filter {'saved' if final_equity_cvd > final_equity_ma else 'cost'} {abs(diff):.2f}% vs MA-only")

# ============================================================
# 7. RISK METRICS
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

print(f"\n=== Risk Metrics ===")
for label, eq in [("MA-only", equity_ma), ("MA+CVD", equity_cvd), ("Buy&Hold", bh_equity)]:
    mdd = max_drawdown(eq)
    total = ((eq[-1]/eq[0])-1)*100
    print(f"  {label:12s}: Total: {total:+6.2f}%  |  Max Drawdown: {mdd:6.2f}%")

# All crossovers for transparency
df["a"] = df["ma20"] > df["ma200"]
df["p"] = df["a"].shift(1).fillna(False)
cross = df[(df["a"] != df["p"]) & df["p"].notna()]
print(f"\n=== All crossovers (Feb 2024 -> today) ===")
for _, r in cross.iterrows():
    d = "BUY" if r["a"] else "SELL"
    print(f"  {r['dt'].strftime('%Y-%m-%d')}  {d}  @ ${r['close']:,.2f}  spread={r['ma20']-r['ma200']:+.2f}")

# ============================================================
# 8. PLOT — 3-panel: equity + price + CVD
#    All slices use SAME start_idx, same df slice, no mismatched xlim
# ============================================================
plt.close("all")

# Use the actual date range for the title
date_range_start = df["dt"].iloc[start_idx].strftime("%b %Y")
date_range_end   = df["dt"].iloc[-1].strftime("%b %Y")

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True,
                                      gridspec_kw={"height_ratios": [2, 2]})
fig.suptitle(f"BTC Momentum: MA-only vs MA+CVD Divergence\n({date_range_start} – {date_range_end})",
             fontsize=14, fontweight="bold")

# ── Top: Equity curves ──────────────────────────────────────
ax1.plot(dates_ma, equity_ma,   label="MA20/MA200 Only",        color="#2196F3", linewidth=2)
ax1.plot(dates_ma, equity_cvd,  label="MA20/MA200 + CVD Filter", color="#FF9800", linewidth=2, alpha=0.9)
ax1.plot(dates_ma, bh_equity,   label="Buy & Hold",            color="grey",   linewidth=1.5, alpha=0.7)
ax1.set_ylabel("Portfolio Value ($)")
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
ax1.legend(loc="upper right", ncol=3)
ax1.set_title("Equity Curve Comparison")
ax1.grid(True, alpha=0.3)

for t in trades_ma:
    color  = "green" if t["type"] == "BUY" else "red"
    marker = "^" if t["type"] == "BUY" else "v"
    ax1.axvline(t["date"], color=color, linestyle="--", alpha=0.4, linewidth=0.8)
    ax1.scatter([t["date"]], [t["equity"]], color=color, marker=marker, s=80, zorder=5)

# ── Middle: Price + MAs ──────────────────────────────────────
# Slice df ONCE — same slice used for price, MA20, MA200, fill_between
price_slice = df["close"].iloc[start_idx:].reset_index(drop=True)
ma20_slice   = df["ma20"].iloc[start_idx:].reset_index(drop=True)
ma200_slice  = df["ma200"].iloc[start_idx:].reset_index(drop=True)
dt_slice     = df["dt"].iloc[start_idx:].reset_index(drop=True)

ax2.plot(dt_slice, price_slice,  color="black", linewidth=1.5, label="BTC Price")
ax2.plot(dt_slice, ma20_slice,    color="#2196F3", linewidth=1,  label="MA20", alpha=0.8)
ax2.plot(dt_slice, ma200_slice,   color="red",    linewidth=1,  label="MA200", alpha=0.8)
ax2.fill_between(dt_slice, ma20_slice, ma200_slice,
                 where=(ma20_slice >= ma200_slice),
                 color="green", alpha=0.1, label="MA above")
ax2.fill_between(dt_slice, ma20_slice, ma200_slice,
                 where=(ma20_slice < ma200_slice),
                 color="red", alpha=0.1, label="MA below")
ax2.set_ylabel("BTC Price ($)")
ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
ax2.legend(loc="upper right", fontsize=9)
ax2.set_title("Price + MA20/MA200 + CVD (right axis)")
ax2.grid(True, alpha=0.3)

for t in trades_ma:
    color  = "green" if t["type"] == "BUY" else "red"
    marker = "^" if t["type"] == "BUY" else "v"
    ax2.scatter([t["date"]], [t["price"]], color=color, marker=marker, s=60, zorder=5)

# ── Bottom: Raw CVD merged into price panel ─────────────────
ax2_cvd = ax2.twinx()  # separate y-axis for CVD

cvd_slice = df["cvd"].iloc[start_idx:].reset_index(drop=True)
cvd_clean = np.nan_to_num(cvd_slice.values, nan=0.0)

ax2_cvd.plot(dt_slice, cvd_clean, color="#FF9800", linewidth=1.5, label="CVD", alpha=0.85)
ax2_cvd.fill_between(dt_slice, cvd_clean, 0,
                     where=(cvd_clean >= 0), color="green", alpha=0.12)
ax2_cvd.fill_between(dt_slice, cvd_clean, 0,
                     where=(cvd_clean < 0),  color="red",   alpha=0.12)
ax2_cvd.set_ylabel("CVD (BTC)", color="#FF9800")
ax2_cvd.tick_params(axis="y", labelcolor="#FF9800")
ax2_cvd.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
ax2_cvd.axhline(0, color="grey", linewidth=0.8, alpha=0.4)

# Divergence markers on CVD y-axis
div_mask = df["cvd_diverging"].iloc[start_idx:].reset_index(drop=True)
for j, (is_div, d) in enumerate(zip(div_mask, dt_slice)):
    if is_div:
        ax2.axvline(d, color="orange", linestyle=":", alpha=0.6, linewidth=0.8)

# CVD legend — merge lines from both y-axes
lines1, labels1 = ax2.get_legend_handles_labels()
lines2, labels2 = ax2_cvd.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=9)

# ── Shared X-axis format ───────────────────────────────────
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

# Auto-range from data
ax1.set_xlim(dt_slice.iloc[0], dt_slice.iloc[-1])
ax2.set_xlim(dt_slice.iloc[0], dt_slice.iloc[-1])

plt.tight_layout()
plt.savefig("btc_cvd_equity_curve.png", dpi=120, bbox_inches="tight")
print(f"\nChart saved: btc_cvd_equity_curve.png")

print("\nDone.")