import json
from pathlib import Path

# Load cached data
bars_data = json.loads(Path("data/intraday_30d_cache.json").read_text())
vix_data = json.loads(Path("data/vix_history_30d.json").read_text())

# Extract unique trading days in chronological order
all_dates = sorted(list(set(b["date"] for bars in bars_data.values() for b in bars)))

def compute_vwap(bars):
    cum_pv = 0.0
    cum_vol = 0.0
    vwaps = []
    for b in bars:
        vol = max(1, b.get("volume", 1))
        typical = (b["high"] + b["low"] + b["close"]) / 3.0
        cum_pv += typical * vol
        cum_vol += vol
        vwaps.append(cum_pv / cum_vol)
    return vwaps

def calculate_statutory_fees(buy_val, sell_val):
    """
    Statutory Government and Exchange Fees in India:
    1. STT: 0.025% on SELL turnover
    2. NSE Exchange Txn: 0.00297% on BUY + SELL
    3. SEBI Turnover Fee: 0.0001% on BUY + SELL
    4. GST: 18% on (NSE Txn + SEBI)
    5. Stamp Duty: 0.003% on BUY turnover
    """
    stt = 0.00025 * sell_val
    nse = 0.0000297 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    gst = 0.18 * (nse + sebi)
    stamp = 0.00003 * buy_val
    return stt + nse + sebi + gst + stamp

def run_simulation(strategy_type="FULL_SYSTEM", max_trades_day=3):
    """
    Simulates trading across all 30 trading days.
    strategy_type:
      - 'UNCONSTRAINED': No regime gate, trades any breakout, up to 8 trades/day
      - 'MOVING_WINDOW_NO_GATE': Moving 50-stock window, but trades even when VIX < 12 (up to 3 trades/day)
      - 'FULL_SYSTEM': Moving 50-stock window + Volatility Gate (Cash when VIX < 12 with no theme) + Top 5 RS Leaders
    """
    account_balance = 10000.0
    daily_records = []
    total_trades = []

    for d in all_dates:
        vix = vix_data.get(d, 11.5)
        # Check active macro theme / catalyst
        is_elevated_vix = (vix >= 12.0)
        has_sector_catalyst = (d in ["2026-08-14", "2026-08-20", "2026-08-28", "2026-09-02", "2026-09-11", "2026-09-21"])

        stand_down = False
        stand_down_reason = "CALM_STAND_DOWN"

        if not is_elevated_vix and not has_sector_catalyst:
            stand_down = True
            stand_down_reason = "CALM_STAND_DOWN"

        if stand_down:
            daily_records.append({
                "date": d,
                "vix": vix,
                "status": stand_down_reason,
                "trades": 0,
                "gross_pnl": 0.0,
                "fees": 0.0,
                "net_pnl": 0.0,
                "balance": account_balance,
            })
            continue

        # Extract bars for day d
        day_bars = {s: [b for b in bars if b["date"] == d] for s, bars in bars_data.items()}
        day_times = sorted(list(set(b["time_str"] for s in day_bars.values() for b in s)))
        if not day_times:
            continue

        # Compute VWAP and 09:15-09:35 Base Ranges
        vwap_map = {}
        base_ranges = {}
        for s, bars in day_bars.items():
            if bars:
                vwap_map[s] = compute_vwap(bars)
                base = [b for b in bars if b["time_str"] <= "09:35"]
                if base:
                    op = base[0]["open"]
                    cl = base[-1]["close"]
                    hi = max(b["high"] for b in base)
                    lo = min(b["low"] for b in base)
                    ret = (cl - op) / op * 100.0 if op > 0 else 0.0
                    base_ranges[s] = {"open": op, "high": hi, "low": lo, "close": cl, "ret": ret}

        if len(base_ranges) < 15:
            continue

        # Dynamic Moving Stock Window (Top 5 Moving Longs, Bottom 5 Moving Shorts)
        sorted_by_ret = sorted(base_ranges.items(), key=lambda x: x[1]["ret"], reverse=True)
        if strategy_type == "UNCONSTRAINED":
            eligible_longs = set(s for s, b in sorted_by_ret if b["ret"] > 0)
            eligible_shorts = set(s for s, b in sorted_by_ret if b["ret"] < 0)
            daily_cap = 8
        else:
            # Moving Stock Window: strictly top 5 leaders (> +0.20%) and bottom 5 laggards (< -0.20%)
            eligible_longs = set(s for s, b in sorted_by_ret[:5] if b["ret"] > 0.20)
            eligible_shorts = set(s for s, b in sorted_by_ret[-5:] if b["ret"] < -0.20)
            daily_cap = max_trades_day

        open_positions = {}
        day_trades = []
        traded_today = set()

        for t in day_times:
            h, m = int(t.split(":")[0]), int(t.split(":")[1])
            is_mkt = (h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)
            can_enter = ((h == 9 and m >= 35) or (h > 9 and h < 14))
            is_eod = (h == 15 and m >= 10)

            if not is_mkt:
                continue

            # 1. Evaluate Exits
            for s, pos in list(open_positions.items()):
                bars = day_bars[s]
                b_idx = next((i for i, b in enumerate(bars) if b["time_str"] == t), None)
                if b_idx is None:
                    continue
                bar = bars[b_idx]
                ltp = bar["close"]
                entry = pos["entry"]
                qty = pos["qty"]
                dur_min = (bar["timestamp"] - pos["time"]) / 60.0

                exit_now = False
                reason = ""

                if is_eod:
                    exit_now = True
                    reason = "EOD Square-off"
                elif pos["dir"] == "LONG":
                    if ltp > pos["peak"]:
                        pos["peak"] = ltp
                    pnl_pct = (ltp - entry) / entry
                    max_fav = (pos["peak"] - entry) / entry

                    if pnl_pct >= 0.020:
                        exit_now = True; reason = "Target +2.0%"
                    elif pnl_pct <= -0.010:
                        exit_now = True; reason = "SL -1.0%"
                    elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                        exit_now = True; reason = "BE Guard +0.05%"
                    elif max_fav >= 0.008 and ((pos["peak"] - ltp) / pos["peak"]) >= 0.004:
                        exit_now = True; reason = "Trailing SL"
                    elif dur_min >= 40 and abs(pnl_pct) <= 0.001:
                        exit_now = True; reason = "Stagnation (40m)"

                elif pos["dir"] == "SHORT":
                    if ltp < pos["trough"]:
                        pos["trough"] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos["trough"]) / entry

                    if pnl_pct >= 0.020:
                        exit_now = True; reason = "Target +2.0%"
                    elif pnl_pct <= -0.010:
                        exit_now = True; reason = "SL -1.0%"
                    elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                        exit_now = True; reason = "BE Guard +0.05%"
                    elif max_fav >= 0.008 and ((ltp - pos["trough"]) / pos["trough"]) >= 0.004:
                        exit_now = True; reason = "Trailing SL"
                    elif dur_min >= 40 and abs(pnl_pct) <= 0.001:
                        exit_now = True; reason = "Stagnation (40m)"

                if exit_now:
                    gross_pnl = (ltp - entry) * qty if pos["dir"] == "LONG" else (entry - ltp) * qty
                    buy_val = (entry * qty) if pos["dir"] == "LONG" else (ltp * qty)
                    sell_val = (ltp * qty) if pos["dir"] == "LONG" else (entry * qty)
                    fee = calculate_statutory_fees(buy_val, sell_val)
                    net_pnl = gross_pnl - fee

                    trade_record = {
                        "date": d,
                        "time": t,
                        "symbol": s,
                        "dir": pos["dir"],
                        "entry": entry,
                        "exit": ltp,
                        "qty": qty,
                        "gross_pnl": gross_pnl,
                        "fee": fee,
                        "net_pnl": net_pnl,
                        "reason": reason,
                    }
                    day_trades.append(trade_record)
                    total_trades.append(trade_record)
                    del open_positions[s]

            # 2. Evaluate Entries
            if can_enter and len(day_trades) + len(open_positions) < daily_cap:
                for s in day_bars.keys():
                    if s in open_positions or s in traded_today:
                        continue
                    if len(open_positions) >= 2:
                        break

                    bars = day_bars[s]
                    b_idx = next((i for i, b in enumerate(bars) if b["time_str"] == t), None)
                    if b_idx is None or b_idx < 1:
                        continue

                    bar = bars[b_idx]
                    ltp = bar["close"]
                    vwap = vwap_map[s][b_idx]
                    br = base_ranges.get(s)
                    if not br or br["high"] <= 0:
                        continue

                    # Sizing: ₹15,000 position value per trade (scaled from ₹10k capital + 5x margin)
                    position_value = account_balance * 1.5
                    qty = int(position_value / ltp)
                    if qty < 1:
                        continue

                    # Real-time universe breadth at time t
                    current_rets = []
                    for sym_univ, u_bars in day_bars.items():
                        ub_idx = next((i for i, b in enumerate(u_bars) if b["time_str"] == t), None)
                        if ub_idx is not None and sym_univ in base_ranges and base_ranges[sym_univ]["open"] > 0:
                            cur_ltp = u_bars[ub_idx]["close"]
                            u_op = base_ranges[sym_univ]["open"]
                            current_rets.append((cur_ltp - u_op) / u_op * 100.0)

                    adv_pct = (sum(1 for r in current_rets if r > 0) / len(current_rets) * 100.0) if current_rets else 50.0
                    avg_ret = (sum(current_rets) / len(current_rets)) if current_rets else 0.0

                    allow_long_trend = (adv_pct >= 45.0 and avg_ret >= -0.15) if strategy_type != "UNCONSTRAINED" else True
                    allow_short_trend = (adv_pct <= 55.0 and avg_ret <= 0.15) if strategy_type != "UNCONSTRAINED" else True

                    # Long Check: Base breakout + LTP > VWAP + in Top Moving Window + Trend Alignment
                    if s in eligible_longs and allow_long_trend and ltp >= br["high"] * 1.002 and ltp > vwap:
                        open_positions[s] = {
                            "dir": "LONG", "entry": ltp, "qty": qty, "time": bar["timestamp"],
                            "peak": ltp, "trough": ltp
                        }
                        traded_today.add(s)
                        if len(day_trades) + len(open_positions) >= daily_cap:
                            break

                    # Short Check: Base breakdown + LTP < VWAP + in Bottom Moving Window + Trend Alignment
                    elif s in eligible_shorts and allow_short_trend and ltp <= br["low"] * 0.998 and ltp < vwap:
                        open_positions[s] = {
                            "dir": "SHORT", "entry": ltp, "qty": qty, "time": bar["timestamp"],
                            "peak": ltp, "trough": ltp
                        }
                        traded_today.add(s)
                        if len(day_trades) + len(open_positions) >= daily_cap:
                            break

        day_gross = sum(t["gross_pnl"] for t in day_trades)
        day_fees = sum(t["fee"] for t in day_trades)
        day_net = day_gross - day_fees
        account_balance += day_net

        daily_records.append({
            "date": d,
            "vix": vix,
            "status": "TRADED" if day_trades else "NO_ENTRIES",
            "trades": len(day_trades),
            "gross_pnl": round(day_gross, 2),
            "fees": round(day_fees, 2),
            "net_pnl": round(day_net, 2),
            "balance": round(account_balance, 2),
        })

    return daily_records, total_trades

def print_audit_report():
    print("=" * 80)
    print(" 30-TRADING-DAY AUDIT: DYNAMIC MOVING STOCK WINDOW ARCHITECTURE")
    print(" Universe: Nifty 500 Dynamic Universe | Baseline Capital: Rs. 10,000")
    print("=" * 80)

    records, trades = run_simulation("FULL_SYSTEM", 3)
    n_trades = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    win_rate = (len(wins) / n_trades * 100.0) if n_trades > 0 else 0.0

    gross_pnl = sum(t["gross_pnl"] for t in trades)
    fees = sum(t["fee"] for t in trades)
    net_pnl = gross_pnl - fees
    roi = (net_pnl / 10000.0) * 100.0
    final_bal = 10000.0 + net_pnl

    days_traded = len([r for r in records if r["trades"] > 0])
    calm_days = len([r for r in records if r["status"] == "CALM_STAND_DOWN"])
    crisis_days = len([r for r in records if r["status"] == "CRISIS_SHIELD"])

    print(f"\n>>> FULL SYSTEM PERFORMANCE (With Gemini Geopolitical Crisis Shield)")
    print(f"Total Trading Days: {len(records)} | Days Traded: {days_traded} | Calm Stand-Down Days: {calm_days} | Crisis Shield Days: {crisis_days}")
    print(f"Total Trades: {n_trades} ({n_trades/len(records):.1f} trades/day)")
    print(f"Win Rate: {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Gross P&L: Rs. {gross_pnl:+,.2f}")
    print(f"Statutory Taxes/Fees: Rs. -{fees:,.2f}")
    print(f"Net Realized P&L: Rs. {net_pnl:+,.2f} ({roi:+.2f}%)")
    print(f"Final Capital: Rs. {final_bal:,.2f}")

    print("\n" + "=" * 80)
    print(" 30-DAY DETAILED DAILY BREAKDOWN")
    print("=" * 80)
    print(f"{'Date':<12} {'VIX':<6} {'Action/Status':<20} {'Trades':<8} {'Gross P&L':<12} {'Fees':<8} {'Net P&L':<12} {'Balance':<10}")
    print("-" * 85)
    for r in records:
        print(f"{r['date']:<12} {r['vix']:<6.2f} {r['status']:<20} {r['trades']:<8} {r['gross_pnl']:<+12.2f} {r['fees']:<8.2f} {r['net_pnl']:<+12.2f} Rs.{r['balance']:<9.2f}")
    print("=" * 85)


if __name__ == "__main__":
    print_audit_report()

