"""
BTC MA20/MA200 + CVD Entry Filter + Trailing Stop (v7)
=======================================================
Improvement #2: CVD as entry gate — only enter golden crosses confirmed by CVD

- Entry: MA20 crosses above MA200 AND CVD trend is rising (z-score > threshold)
- Exit:  10% trailing stop OR MA death cross (whichever hits first)
- Tests: multiple CVD entry thresholds to find optimal gate

Run: python btc_cvd_entry_filter.py
Output: btc_cvd_entry_filter_equity.png
"""

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ============================================================
# 1. FETCH + DEDUP (same as v6)
# ============================================================
print("Fetching BTCUSDT daily klines from Binance...")
FETCH_START_MS = 1692662400000

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

seen = set()
deduped = []
for k in all_klines:
    if k[0] not in seen:
        seen.add(k[0])
        deduped.append(k)

print(f"Fetched {len(all_klines)} klines, {len(deduped)} unique")

df = pd.DataFrame(deduped, columns=[
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_base", "taker_buy_quote", "ignore"
])
df["dt"] = pd.to_datetime(df["open_time"], unit="ms")
for col in ["close", "open", "high", "low", "volume", "taker_buy_base"]:
    df[col] = df[col].astype(float)
df = df.sort_values("dt").reset_index(drop=True)

# ============================================================
# 2. COMPUTE INDICATORS
# ============================================================
print(f"Data: {df['dt'].iloc[0].strftime('%Y-%m-%d')} → {df['dt'].iloc[-1].strftime('%Y-%m-%d')} ({len(df)} days)")

df["ma20"] = df["close"].rolling(window=20).mean()
df["ma200"] = df["close"].rolling(window=200).mean()

# CVD from Binance taker_buy_base (same as v5/v6)
df["delta"] = 2 * df["taker_buy_base"] - df["volume"]
df["cvd"] = df["delta"].cumsum()

# CVD trend indicators (multiple for testing)
df["cvd_ma20"] = df["cvd"].rolling(20).mean()
df["cvd_std20"] = df["cvd"].rolling(20).std()
df["cvd_zscore"] = (df["cvd"] - df["cvd_ma20"]) / df["cvd_std20"]   # z-score — is CVD above/below its 20d avg?

# CVD 20-day rate of change (is CVD actually rising?)
df["cvd_roc20"] = df["cvd"].pct_change(periods=20) * 100

# CVD slope (linear regression over last 20 days) — most robust measure
df["cvd_slope"] = np.nan
for i in range(20, len(df)):
    y = df["cvd"].iloc[i-19:i+1].values
    if len(y) > 1 and not np.any(np.isnan(y)):
        x = np.arange(len(y))
        slope, _ = np.polyfit(x, y, 1)
        df.loc[df.index[i], "cvd_slope"] = slope

# MA crossover signals
df["ma_cross"] = 0
for i in range(200, len(df) - 1):
    if pd.notna(df["ma20"].iloc[i]) and pd.notna(df["ma200"].iloc[i]) and pd.notna(df["ma20"].iloc[i-1]):
        if df["ma20"].iloc[i-1] < df["ma200"].iloc[i-1] and df["ma20"].iloc[i] >= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = 1
        elif df["ma20"].iloc[i-1] > df["ma200"].iloc[i-1] and df["ma20"].iloc[i] <= df["ma200"].iloc[i]:
            df.loc[df.index[i], "ma_cross"] = -1

first_valid = df[df["ma200"].notna()]["dt"].iloc[0]
print(f"First valid MA200: {first_valid.strftime('%Y-%m-%d')}")

# ============================================================
# 3. BACKTEST ENGINE — CVD entry gate + trailing stop exit
# ============================================================
BACKTEST_START = "2024-01-01"
TRAIL_PCT = 0.10  # v6 winner

def cvd_entry_allowed(row, method, threshold):
    """
    Returns True if CVD confirms the entry.
    method: "none" (no filter), "zscore", "roc", "slope"
    """
    if method == "none":
        return True
    elif method == "zscore":
        # CVD above its 20-day average — momentum is neutral or positive
        return row["cvd_zscore"] > threshold if pd.notna(row["cvd_zscore"]) else True
    elif method == "roc":
        # CVD has risen over past 20 days
        return row["cvd_roc20"] > threshold if pd.notna(row["cvd_roc20"]) else True
    elif method == "slope":
        # CVD 20d linear trend is positive
        return row["cvd_slope"] > threshold if pd.notna(row["cvd_slope"]) else True
    return True


def run_backtest(df, cvd_method="none", cvd_threshold=0.0, start_date=BACKTEST_START):
    start_idx = df[df["dt"] >= pd.to_datetime(start_date)].index[0]
    in_position = False
    entry_price = 0.0
    shares_held = 0.0
    trail_high = 0.0
    stop_level = 0.0
    trades = []
    equity = 10000.0
    equity_curve = []
    skipped = 0   # count golden crosses skipped by CVD filter

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1] if i > 0 else None

        if pd.isna(row["ma20"]) or pd.isna(row["ma200"]):
            equity_curve.append(equity)
            continue

        # ---- ENTRY: MA golden cross + CVD gate ----
        if prev is not None and not in_position:
            ma_crossing_up = (prev["ma_cross"] == 0 and row["ma_cross"] == 1)
            if ma_crossing_up:
                if cvd_entry_allowed(row, cvd_method, cvd_threshold):
                    in_position = True
                    entry_price = row["close"]
                    shares_held = equity / entry_price
                    trail_high = entry_price
                    stop_level = trail_high * (1 - TRAIL_PCT)
                    trades.append({
                        "type": "BUY", "date": row["dt"],
                        "price": entry_price, "equity": equity
                    })
                else:
                    skipped += 1

        # ---- EXIT: trailing stop OR death cross ----
        elif prev is not None and in_position:
            ma_crossing_down = (prev["ma_cross"] == 0 and row["ma_cross"] == -1)

            trail_high = max(trail_high, row["close"])
            stop_level = trail_high * (1 - TRAIL_PCT)
            stop_breached = (row["close"] <= stop_level)

            if ma_crossing_down or stop_breached:
                exit_price = row["close"]
                equity = shares_held * exit_price
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                reason = "DEATH CROSS" if ma_crossing_down else "TRAILING STOP"
                trades.append({
                    "type": "SELL", "date": row["dt"],
                    "price": exit_price, "pnl_pct": pnl_pct,
                    "equity": equity, "reason": reason
                })
                in_position = False
                shares_held = 0.0

        # ---- Mark-to-market ----
        if in_position:
            equity_curve.append(shares_held * row["close"])
        else:
            equity_curve.append(equity)

    # Close open position
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
    return equity_curve, trades, dates_out, skipped


# ============================================================
# 4. TEST ENTRY FILTER VARIANTS
# ============================================================
print("\nRunning strategies...")

# Define variants to test
variants = [
    ("No CVD Filter (v6)",        "none",   0.0),
    ("CVD Z-Score > 0",           "zscore", 0.0),    # CVD above 20d avg
    ("CVD Z-Score > -0.5",        "zscore", -0.5),   # looser — CVD not dropping hard
    ("CVD Z-Score > 0.5",         "zscore", 0.5),    # stricter — CVD clearly rising
    ("CVD ROC(20d) > 0%",         "roc",    0.0),    # CVD grew over last 20 days
    ("CVD Slope > 0",             "slope",  0.0),    # CVD 20d trend is up
]

results = {}
for label, method, threshold in variants:
    eq, tr, dt, sk = run_backtest(df, cvd_method=method, cvd_threshold=threshold)
    results[label] = (eq, tr, dt, sk)

# Buy & hold
start_idx = df[df["dt"] >= pd.to_datetime(BACKTEST_START)].index[0]
bh_prices = df.iloc[start_idx:]["close"].values
bh_equity = 10000 * (bh_prices / bh_prices[0])

# ============================================================
# 5. PRINT RESULTS
# ============================================================
def max_drawdown(eq):
    peak = np.maximum.accumulate(eq)
    return ((eq - peak) / peak).min() * 100

def sharpe(eq, rf=0.0):
    rets = np.diff(eq) / eq[:-1]
    if len(rets) == 0 or rets.std() == 0:
        return 0.0
    return (rets.mean() * 365 - rf) / (rets.std() * np.sqrt(365))

def print_trades(trades, label, skipped):
    print(f"\n=== {label} ===")
    print(f"  Golden crosses skipped by CVD: {skipped}")
    for t in trades:
        reason = f"  [{t.get('reason', '')}]" if t.get("reason") else ""
        if t["type"] == "BUY":
            print(f"  BUY   {t['date'].strftime('%Y-%m-%d')}  @ ${t['price']:>10,.2f}{reason}")
        elif t["type"] in ("SELL", "CLOSE"):
            print(f"  {t['type']:5} {t['date'].strftime('%Y-%m-%d')}  @ ${t['price']:>10,.2f}  |  PnL: {t['pnl_pct']:+.2f}%{reason}")

for label, (eq, tr, dt, sk) in results.items():
    print_trades(tr, label, sk)

# Performance table
print(f"\n╔═══════════════════════════════════════════════════════════════════════════════════════╗")
print(f"║  CVD ENTRY FILTER RESULTS  ({BACKTEST_START} → {df['dt'].iloc[-1].strftime('%Y-%m-%d')})                            ║")
print(f"╠══════════════════════════════════════╤═══════════╤═══════════╤═══════════╤═══════════╣")
print(f"║  Strategy                            │ Total Ret │ Max Draw  │  Sharpe   │  Trades   ║")
print(f"╠══════════════════════════════════════╪═══════════╪═══════════╪═══════════╪═══════════╣")

for label, (eq, tr, dt, sk) in results.items():
    total_ret = (eq[-1] / 10000 - 1) * 100
    mdd = max_drawdown(np.array(eq))
    shr = sharpe(np.array(eq))
    n_trades = len([t for t in tr if t["type"] == "BUY"])
    marker = " 🔥" if label == "No CVD Filter (v6)" else ""
    print(f"║  {label:<35s}│ {total_ret:+8.2f}% │ {mdd:+8.2f}% │ {shr:+8.3f} │ {n_trades:>8d}  ║{marker}")

bh_ret = (bh_equity[-1] / 10000 - 1) * 100
bh_mdd = max_drawdown(np.array(bh_equity))
bh_shr = sharpe(np.array(bh_equity))
print(f"║  {'Buy & Hold':<35s}│ {bh_ret:+8.2f}% │ {bh_mdd:+8.2f}% │ {bh_shr:+8.3f} │ {'—':>8}  ║")
print(f"╚══════════════════════════════════════╧═══════════╧═══════════╧═══════════╧═══════════╝")

# ============================================================
# 6. PLOT — 2 panels
# ============================================================
plt.close("all")
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), sharex=True,
                                gridspec_kw={"height_ratios": [2, 2]})

dt_slice = df["dt"].iloc[start_idx:].reset_index(drop=True)
price_slice = df["close"].iloc[start_idx:].values
ma20_slice = df["ma20"].iloc[start_idx:].values
ma200_slice = df["ma200"].iloc[start_idx:].values
cvd_slice = df["cvd_zscore"].iloc[start_idx:].values

# --- Top panel: equity curves — v6 baseline + best CVD variant ---
v6_eq = results["No CVD Filter (v6)"][0]
v6_dates = results["No CVD Filter (v6)"][2]

# Find best CVD variant (by return/drawdown ratio)
best_cvd_label = None
best_cvd_score = -999
for label in results:
    if label == "No CVD Filter (v6)":
        continue
    eq = results[label][0]
    ret = (eq[-1] / 10000 - 1) * 100
    mdd = abs(max_drawdown(np.array(eq)))
    score = ret / mdd if mdd > 0 else ret
    if score > best_cvd_score:
        best_cvd_score = score
        best_cvd_label = label

best_cvd_eq = results[best_cvd_label][0]
best_cvd_dates = results[best_cvd_label][2]

ax1.plot(v6_dates.values, v6_eq, label="v6 Baseline (no CVD filter)",
         color="#2196F3", linewidth=2.0)
ax1.plot(best_cvd_dates.values, best_cvd_eq, label=f"{best_cvd_label}",
         color="#FF9800", linewidth=1.8)
ax1.plot(v6_dates.values, bh_equity, label="Buy & Hold",
         color="grey", linewidth=1.2, alpha=0.6, linestyle="--")
ax1.set_ylabel("Portfolio Value ($)", fontsize=11)
ax1.set_title("CVD Entry Filter — v6 Baseline vs Best CVD Variant", fontsize=13)
ax1.legend(loc="upper left", fontsize=9)
ax1.grid(alpha=0.3)
ax1.set_xlim(dt_slice.iloc[0], dt_slice.iloc[-1])

# --- Bottom panel: price + MAs + CVD z-score with trade markers ---
ax2.plot(dt_slice, price_slice, color="black", linewidth=1.2, label="BTC Close")
ax2.plot(dt_slice, ma20_slice, color="#2196F3", linewidth=0.9, label="MA20", alpha=0.8)
ax2.plot(dt_slice, ma200_slice, color="red", linewidth=0.9, label="MA200", alpha=0.8)
ax2.fill_between(dt_slice.values, ma20_slice, ma200_slice,
                 where=(ma20_slice >= ma200_slice), color="green", alpha=0.1)
ax2.fill_between(dt_slice.values, ma20_slice, ma200_slice,
                 where=(ma20_slice < ma200_slice), color="red", alpha=0.1)

# CVD z-score on twin axis
ax2_cvd = ax2.twinx()
ax2_cvd.plot(dt_slice, cvd_slice, color="#FF9800", linewidth=1.0, label="CVD Z-Score", alpha=0.7)
ax2_cvd.axhline(0, color="#FF9800", linewidth=0.7, linestyle="--", alpha=0.5)
ax2_cvd.axhline(-1.5, color="red", linewidth=0.5, linestyle=":", alpha=0.4)
ax2_cvd.set_ylabel("CVD Z-Score", color="#FF9800", fontsize=10)
ax2_cvd.tick_params(axis="y", labelcolor="#FF9800")

# Trade markers (best CVD variant)
best_trades = results[best_cvd_label][1]
for t in best_trades:
    if t["type"] == "BUY":
        ax2.axvline(t["date"], color="green", linestyle="-", alpha=0.5, linewidth=1.0)
        ax2.scatter(t["date"], t["price"], color="green", marker="^", s=100, zorder=5)
    elif t["type"] in ("SELL", "CLOSE"):
        is_trail = "TRAILING" in t.get("reason", "")
        color = "#FF9800" if is_trail else "red"
        ax2.axvline(t["date"], color=color, linestyle="-", alpha=0.5, linewidth=1.0)
        ax2.scatter(t["date"], t["price"], color=color, marker="v" if not is_trail else "s",
                    s=100, zorder=5)

# Merge legends
lines1, labels1 = ax2.get_legend_handles_labels()
lines2, labels2 = ax2_cvd.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8)
ax2.set_xlabel("Date")
ax2.grid(alpha=0.3)
ax2.set_xlim(dt_slice.iloc[0], dt_slice.iloc[-1])

plt.tight_layout()
plt.savefig("btc_cvd_entry_filter_equity.png", dpi=120, bbox_inches="tight")
print(f"\nChart saved: btc_cvd_entry_filter_equity.png")
print("Done.")
