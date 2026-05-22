"""
BTC MA20/MA200 Momentum Backtest with Trailing Stop-Loss (v6)
=============================================================
Improvement #1: Trailing stop-loss to reduce drawdown

- Entry: MA20 crosses above MA200 (golden cross) → BUY
- Exit:  MA20 crosses below MA200 (death cross) OR trailing stop breach
- Trailing stop: X% below highest close since entry, ratchets UP only
- Tests: multiple stop levels (10%, 15%, 20%) to find optimal

Run: python btc_trailing_stop_backtest.py
Output: btc_trailing_stop_equity_curve.png
"""

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ============================================================
# 1. FETCH DAILY KLINES FROM BINANCE (forward fetch, deduped)
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
for col in ["close", "open", "high", "low", "volume"]:
    df[col] = df[col].astype(float)
df = df.sort_values("dt").reset_index(drop=True)

# ============================================================
# 2. COMPUTE INDICATORS
# ============================================================
print(f"Data loaded: {df['dt'].iloc[0].strftime('%Y-%m-%d')} -> {df['dt'].iloc[-1].strftime('%Y-%m-%d')} ({len(df)} days)")

df["ma20"] = df["close"].rolling(window=20).mean()
df["ma200"] = df["close"].rolling(window=200).mean()

# Daily ATR(14) — used for dynamic trailing stop reference
df["tr"] = np.maximum(
    df["high"] - df["low"],
    np.maximum(
        abs(df["high"] - df["close"].shift(1)),
        abs(df["low"] - df["close"].shift(1))
    )
)
df["atr14"] = df["tr"].rolling(14).mean()

first_valid = df[df["ma200"].notna()]["dt"].iloc[0]
print(f"First valid MA200: {first_valid.strftime('%Y-%m-%d')}")

# ============================================================
# 3. DEFINE MA CROSSOVER SIGNALS
# ============================================================
df["ma_cross"] = 0
for i in range(200, len(df) - 1):
    if pd.notna(df["ma20"].iloc[i]) and pd.notna(df["ma200"].iloc[i]) and pd.notna(df["ma20"].iloc[i-1]):
        if df["ma20"].iloc[i-1] < df["ma200"].iloc[i-1] and df["ma20"].iloc[i] >= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = 1   # golden cross
        elif df["ma20"].iloc[i-1] > df["ma200"].iloc[i-1] and df["ma20"].iloc[i] <= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = -1  # death cross

# ============================================================
# 4. BACKTEST ENGINE — Mark-to-market + trailing stop
# ============================================================
BACKTEST_START = "2024-01-01"

def run_backtest(df, trailing_pct=None, start_date=BACKTEST_START):
    """
    trailing_pct: None = pure MA crossover (baseline)
                  float = trailing stop percentage (e.g., 0.15 = 15% below peak)
    """
    start_idx = df[df["dt"] >= pd.to_datetime(start_date)].index[0]
    in_position = False
    entry_price = 0.0
    shares_held = 0.0
    trail_high = 0.0     # highest close since entry (only moves UP)
    stop_level = 0.0     # trail_high * (1 - trailing_pct)
    trades = []
    equity = 10000.0
    equity_curve = []
    exit_reasons = []    # track WHY each exit happened

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1] if i > 0 else None

        if pd.isna(row["ma20"]) or pd.isna(row["ma200"]):
            equity_curve.append(equity)
            continue

        # ---- ENTRY: MA golden cross ----
        if prev is not None and not in_position:
            ma_crossing_up = (prev["ma_cross"] == 0 and row["ma_cross"] == 1)
            if ma_crossing_up:
                in_position = True
                entry_price = row["close"]
                shares_held = equity / entry_price
                trail_high = entry_price
                stop_level = trail_high * (1 - trailing_pct) if trailing_pct else 0.0
                trades.append({
                    "type": "BUY", "date": row["dt"],
                    "price": entry_price, "equity": equity
                })

        # ---- EXIT: MA death cross OR trailing stop breach ----
        elif prev is not None and in_position:
            ma_crossing_down = (prev["ma_cross"] == 0 and row["ma_cross"] == -1)
            stop_breached = False

            if trailing_pct and trail_high > 0:
                # Update trailing stop based on TODAY's close
                # Check if today's HIGH would have raised the stop first
                trail_high = max(trail_high, row["close"])
                stop_level = trail_high * (1 - trailing_pct)
                # Breach: today's close fell below the stop level
                stop_breached = (row["close"] <= stop_level)

            if ma_crossing_down or stop_breached:
                exit_price = row["close"]
                equity = shares_held * exit_price
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                reason = "DEATH CROSS" if ma_crossing_down else f"TRAILING STOP ({trailing_pct*100:.0f}%)"
                trades.append({
                    "type": "SELL", "date": row["dt"],
                    "price": exit_price, "pnl_pct": pnl_pct,
                    "equity": equity, "reason": reason
                })
                in_position = False
                shares_held = 0.0

        # ---- Mark-to-market equity EVERY day ----
        if in_position:
            equity_curve.append(shares_held * row["close"])
        else:
            equity_curve.append(equity)

    # Close any open position at last price
    if in_position:
        last_close = df.iloc[-1]["close"]
        equity = shares_held * last_close
        pnl_pct = (last_close - entry_price) / entry_price * 100
        equity_curve[-1] = equity
        trades.append({
            "type": "CLOSE", "date": df.iloc[-1]["dt"],
            "price": last_close, "pnl_pct": pnl_pct,
            "equity": equity, "reason": "END OF DATA"
        })

    dates_out = df["dt"].iloc[start_idx:].reset_index(drop=True)
    return equity_curve, trades, dates_out


# ============================================================
# 5. RUN STRATEGIES
# ============================================================
print(f"\nRunning strategies ({BACKTEST_START} → today)...")

# Baseline: MA-only
equity_ma, trades_ma, dates_ma = run_backtest(df, trailing_pct=None)

# Trailing stop variants
trail_levels = [0.10, 0.15, 0.20]  # 10%, 15%, 20%
results = {"MA-only (baseline)": (equity_ma, trades_ma)}

for pct in trail_levels:
    eq, tr, _ = run_backtest(df, trailing_pct=pct)
    results[f"MA + {pct*100:.0f}% Trail"] = (eq, tr)

# Buy & hold
start_idx = df[df["dt"] >= pd.to_datetime(BACKTEST_START)].index[0]
bh_start_price = df.iloc[start_idx]["close"]
bh_prices = df.iloc[start_idx:]["close"].values
bh_equity = 10000 * (bh_prices / bh_start_price)

# ============================================================
# 6. PRINT RESULTS
# ============================================================
def print_trades(trades, label):
    print(f"\n=== {label} Trade Log ===")
    for t in trades:
        reason = f"  [{t.get('reason', '')}]" if t.get("reason") else ""
        if t["type"] == "BUY":
            print(f"  BUY   {t['date'].strftime('%Y-%m-%d')}  @ ${t['price']:>10,.2f}{reason}")
        elif t["type"] in ("SELL", "CLOSE"):
            print(f"  {t['type']:5} {t['date'].strftime('%Y-%m-%d')}  @ ${t['price']:>10,.2f}  |  PnL: {t['pnl_pct']:+.2f}%{reason}")

for label, (eq, tr) in results.items():
    print_trades(tr, label)

print(f"\n╔══════════════════════════════════════════════════════════════════════╗")
print(f"║  PERFORMANCE SUMMARY  ({BACKTEST_START} → {df['dt'].iloc[-1].strftime('%Y-%m-%d')})                       ║")
print(f"╠══════════════════════════════════╤═══════════╤═══════════╤═══════════╣")
print(f"║  Strategy                        │ Total Ret │ Max Draw  │  Sharpe   ║")
print(f"╠══════════════════════════════════╪═══════════╪═══════════╪═══════════╣")

def max_drawdown(eq):
    peak = np.maximum.accumulate(eq)
    return ((eq - peak) / peak).min() * 100

def sharpe(eq, rf=0.0):
    rets = np.diff(eq) / eq[:-1]
    if len(rets) == 0 or rets.std() == 0:
        return 0.0
    ann_ret = rets.mean() * 365
    ann_vol = rets.std() * np.sqrt(365)
    return (ann_ret - rf) / ann_vol

start_equity = 10000.0

for label, (eq, tr) in results.items():
    total_ret = (eq[-1] / start_equity - 1) * 100
    mdd = max_drawdown(np.array(eq))
    shr = sharpe(np.array(eq))
    print(f"║  {label:<33s}│ {total_ret:+8.2f}% │ {mdd:+8.2f}% │ {shr:+8.3f} ║")

# Buy & hold row
bh_ret = (bh_equity[-1] / start_equity - 1) * 100
bh_mdd = max_drawdown(np.array(bh_equity))
bh_shr = sharpe(np.array(bh_equity))
print(f"║  {'Buy & Hold':<33s}│ {bh_ret:+8.2f}% │ {bh_mdd:+8.2f}% │ {bh_shr:+8.3f} ║")
print(f"╚══════════════════════════════════╧═══════════╧═══════════╧═══════════╝")

# ============================================================
# 7. FIND BEST TRAILING STOP %
# ============================================================
best_label = None
best_score = -999
for pct in trail_levels:
    label = f"MA + {pct*100:.0f}% Trail"
    eq = results[label][0]
    ret = (eq[-1] / start_equity - 1) * 100
    mdd = max_drawdown(np.array(eq))
    # Score: reward return, penalize drawdown (Calmar-like)
    score = ret / abs(mdd) if mdd != 0 else ret
    if score > best_score:
        best_score = score
        best_label = label

print(f"\nBest variant by return/drawdown ratio: **{best_label}** (score: {best_score:.3f})")

# ============================================================
# 8. PLOT — 3 panels
# ============================================================
plt.close("all")
fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True,
                          gridspec_kw={"height_ratios": [2, 1, 2]})
ax1, ax2, ax3 = axes

dt_slice = df["dt"].iloc[start_idx:].reset_index(drop=True)
price_slice = df["close"].iloc[start_idx:].values
ma20_slice = df["ma20"].iloc[start_idx:].values
ma200_slice = df["ma200"].iloc[start_idx:].values

# --- Panel 1: Equity curves — ALL strategies ---
colors = {"MA-only (baseline)": "#2196F3", "MA + 10% Trail": "#4CAF50",
          "MA + 15% Trail": "#FF9800", "MA + 20% Trail": "#9C27B0"}

for label, (eq, _) in results.items():
    lw = 2.0 if "baseline" in label else 1.5
    alpha = 1.0 if "15%" in label else 0.75
    ax1.plot(dates_ma.values, eq, label=label, color=colors.get(label, "grey"),
             linewidth=lw, alpha=alpha)

ax1.plot(dates_ma.values, bh_equity, label="Buy & Hold", color="grey",
         linewidth=1.2, alpha=0.6, linestyle="--")
ax1.set_ylabel("Portfolio Value ($)", fontsize=11)
ax1.set_title("BTC MA20/MA200 + Trailing Stop — Equity Curve Comparison", fontsize=13)
ax1.legend(loc="upper left", fontsize=9)
ax1.grid(alpha=0.3)
ax1.set_xlim(dt_slice.iloc[0], dt_slice.iloc[-1])

# --- Panel 2: Drawdown (best variant vs baseline) ---
best_eq = results[best_label][0]
ma_eq = results["MA-only (baseline)"][0]

def dd_series(eq):
    peak = np.maximum.accumulate(eq)
    return (eq - peak) / peak * 100

ax2.fill_between(dates_ma.values, dd_series(ma_eq), 0,
                 color="#2196F3", alpha=0.2, label="MA-only Drawdown")
ax2.plot(dates_ma.values, dd_series(ma_eq), color="#2196F3", linewidth=0.8)
ax2.fill_between(dates_ma.values, dd_series(best_eq), 0,
                 color="#FF9800", alpha=0.3, label=f"{best_label} Drawdown")
ax2.plot(dates_ma.values, dd_series(best_eq), color="#FF9800", linewidth=1.0)
ax2.set_ylabel("Drawdown (%)", fontsize=10)
ax2.set_title("Drawdown Comparison: Baseline vs Best Trailing Stop", fontsize=11)
ax2.legend(fontsize=9)
ax2.grid(alpha=0.3)

# --- Panel 3: Price + MAs + trade markers (best variant) ---
ax3.plot(dt_slice, price_slice, color="black", linewidth=1.2, label="BTC Close")
ax3.plot(dt_slice, ma20_slice, color="#2196F3", linewidth=0.9, label="MA20", alpha=0.8)
ax3.plot(dt_slice, ma200_slice, color="red", linewidth=0.9, label="MA200", alpha=0.8)
ax3.fill_between(dt_slice.values, ma20_slice, ma200_slice,
                 where=(ma20_slice >= ma200_slice), color="green", alpha=0.1)
ax3.fill_between(dt_slice.values, ma20_slice, ma200_slice,
                 where=(ma20_slice < ma200_slice), color="red", alpha=0.1)

best_trades = results[best_label][1]
for t in best_trades:
    if t["type"] == "BUY":
        ax3.axvline(t["date"], color="green", linestyle=":", alpha=0.6, linewidth=1.0)
        ax3.scatter(t["date"], t["price"], color="green", marker="^", s=80, zorder=5)
    elif t["type"] in ("SELL", "CLOSE"):
        # Red = death cross exit, Orange = trailing stop exit
        is_trail = "TRAILING" in t.get("reason", "")
        color = "#FF9800" if is_trail else "red"
        marker = "s" if is_trail else "v"
        ax3.axvline(t["date"], color=color, linestyle=":", alpha=0.6, linewidth=1.0)
        ax3.scatter(t["date"], t["price"], color=color, marker=marker, s=80, zorder=5)

ax3.set_ylabel("BTC Price ($)", fontsize=10)
ax3.set_xlabel("Date")
ax3.set_title(f"Price + MA20/MA200 + Trade Markers — {best_label}", fontsize=11)
ax3.legend(loc="upper left", fontsize=9)
ax3.grid(alpha=0.3)
ax3.set_xlim(dt_slice.iloc[0], dt_slice.iloc[-1])

plt.tight_layout()
plt.savefig("btc_trailing_stop_equity_curve.png", dpi=120, bbox_inches="tight")
print(f"\nChart saved: btc_trailing_stop_equity_curve.png")
print("Done.")
