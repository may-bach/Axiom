#!/usr/bin/env python3
"""
Axiom Comprehensive Market & Strategy Audit Engine
Simulates and evaluates:
  1. Market Truth: Actual sector performance (Defense vs Auto vs Power vs Realty vs Gold)
  2. Scenario 1: Actual Bot (Sentinel-Directed, 09:30 filter, Caution sizing)
  3. Scenario 2: Normal Unconstrained (09:30 filter, Long+Short, 3 positions, Rs.15k)
  4. Scenario 3: Aggressive Mode (09:15 early entry, 0.1% buffer, 5 positions, Rs.20k)
  5. Scenario 4: Aggressive Directional (09:15 early entry, 0.1% buffer, Sentinel directives)
Evaluates across September 22, 2026 (Yesterday) and September 23, 2026 (Today).
"""

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys
import time
import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_FILE = DATA_DIR / "intraday_5d_cache.json"
STOCKS_FILE = DATA_DIR / "stocks.json"
CONFIG_FILE = DATA_DIR / "config.json"
SECTOR_MATRIX_FILE = DATA_DIR / "sector_matrix.json"
IST = timezone(timedelta(hours=5, minutes=30))


def fetch_all_intraday_data(tickers):
    if CACHE_FILE.exists() and (time.time() - CACHE_FILE.stat().st_mtime) < 1800:
        print("[CACHE] Loading cached intraday 1m bars...")
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    print("[DOWNLOAD] Fetching fresh 1-minute intraday bars from market feeds...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }

    all_data = {}
    for s in tickers:
        ytick = "VA-TECH.NS" if s == "WABAG" else f"{s}.NS"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ytick}?interval=1m&range=5d"
        try:
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200:
                res = r.json().get("chart", {}).get("result", [{}])[0]
                timestamps = res.get("timestamp", [])
                quote = res.get("indicators", {}).get("quote", [{}])[0]
                opens = quote.get("open", [])
                highs = quote.get("high", [])
                lows = quote.get("low", [])
                closes = quote.get("close", [])

                bars = []
                for i in range(len(timestamps)):
                    ts = timestamps[i]
                    c = closes[i]
                    if c is None:
                        continue
                    dt_ist = datetime.fromtimestamp(ts, tz=IST)
                    date_str = dt_ist.strftime("%Y-%m-%d")
                    time_str = dt_ist.strftime("%H:%M")
                    o = opens[i] if opens[i] is not None else c
                    h = highs[i] if highs[i] is not None else max(o, c)
                    l = lows[i] if lows[i] is not None else min(o, c)

                    bars.append({
                        "timestamp": ts,
                        "date": date_str,
                        "time_str": time_str,
                        "open": round(float(o), 2),
                        "high": round(float(h), 2),
                        "low": round(float(l), 2),
                        "close": round(float(c), 2),
                    })
                if bars:
                    all_data[s] = bars
        except Exception:
            pass
        time.sleep(0.04)

    CACHE_FILE.write_text(json.dumps(all_data), encoding="utf-8")
    print(f"[DOWNLOAD] Cached 1m bars for {len(all_data)}/{len(tickers)} stocks.")
    return all_data


def analyze_market_reality(all_data, target_date, sector_matrix):
    print(f"\n=================================================================")
    print(f"       ACTUAL MARKET PERFORMANCE & SECTOR REALITY ({target_date})       ")
    print(f"=================================================================")
    
    sector_perf = {}
    stock_perf = {}

    for sym, bars in all_data.items():
        day_bars = [b for b in bars if b["date"] == target_date]
        if not day_bars or len(day_bars) < 20:
            continue

        open_p = day_bars[0]["open"]
        close_p = day_bars[-1]["close"]
        high_p = max(b["high"] for b in day_bars)
        low_p = min(b["low"] for b in day_bars)
        chg_pct = (close_p - open_p) / open_p * 100
        intraday_range_pct = (high_p - low_p) / open_p * 100

        sec_info = sector_matrix.get(sym, {})
        sector = sec_info.get("sector", "OTHER")

        stock_perf[sym] = {
            "open": open_p,
            "close": close_p,
            "high": high_p,
            "low": low_p,
            "chg_pct": chg_pct,
            "range_pct": intraday_range_pct,
            "sector": sector,
        }

        if sector not in sector_perf:
            sector_perf[sector] = []
        sector_perf[sector].append(chg_pct)

    # Sector summary
    print(f"{'SECTOR':<18} | {'AVG RETURN':<12} | {'BULL/BEAR':<12} | {'STOCKS'}")
    print("-" * 65)
    for sec, returns in sorted(sector_perf.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True):
        avg_ret = sum(returns) / len(returns)
        adv = sum(1 for r in returns if r > 0)
        dec = sum(1 for r in returns if r < 0)
        syms = [s for s, d in stock_perf.items() if d["sector"] == sec]
        bias_str = "BULLISH" if avg_ret > 0.2 else ("BEARISH" if avg_ret < -0.2 else "FLAT")
        print(f"{sec:<18} | {avg_ret:+6.2f}%      | {adv}W / {dec}L ({bias_str:<7}) | {', '.join(syms)}")

    return stock_perf, sector_perf


def run_strategy_simulation(
    all_data,
    config,
    target_date,
    directives=None,
    allow_longs=True,
    allow_shorts=True,
    start_time_min=30,      # 30 = 09:30 IST; 15 = 09:15 IST
    breakout_mult=1.0,      # 1.0 = standard config; 0.33 = tight 0.1% buffer
    max_positions=3,
    budget=15000.0,
    stagnation_min=45,
):
    """
    Simulates tick-by-tick trading on target_date.
    """
    # Filter bars to target date
    day_data = {}
    for sym, bars in all_data.items():
        db = [b for b in bars if b["date"] == target_date]
        if db:
            day_data[sym] = db

    all_times = sorted(list(set(b["time_str"] for s in day_data.values() for b in s)))

    high_low = {}
    open_positions = {}
    completed_trades = []

    for t_str in all_times:
        h, m = int(t_str.split(":")[0]), int(t_str.split(":")[1])
        is_market_open = (h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)
        can_enter = (h == 9 and m >= start_time_min) or (h > 9 and h < 15)
        is_eod = (h == 15 and m >= 10)

        if not is_market_open:
            continue

        # 1. Check exits
        to_close = []
        for sym, pos in list(open_positions.items()):
            bar = next((b for b in day_data[sym] if b["time_str"] == t_str), None)
            if not bar:
                continue

            ltp = bar["close"]
            strat = config.get(sym, {})
            target_pct = strat.get("target", 0.02)
            sl_pct = strat.get("sl", 0.01)
            entry_price = pos["entry_price"]
            direction = pos["direction"]
            qty = pos["qty"]
            duration_min = (bar["timestamp"] - pos["entry_time"]) / 60.0

            should_exit = False
            exit_reason = ""

            if is_eod:
                should_exit = True
                exit_reason = "EOD Square-off"
            elif direction == "LONG":
                if ltp > pos["highest_price"]:
                    pos["highest_price"] = ltp

                pnl_pct = (ltp - entry_price) / entry_price
                max_fav = (pos["highest_price"] - entry_price) / entry_price

                if pnl_pct >= target_pct:
                    should_exit = True
                    exit_reason = f"Target {target_pct*100:.1f}%"
                elif pnl_pct <= -sl_pct:
                    should_exit = True
                    exit_reason = f"Stop Loss -{sl_pct*100:.1f}%"
                elif max_fav >= 0.005 and ((pos["highest_price"] - ltp) / pos["highest_price"]) >= 0.005:
                    should_exit = True
                    exit_reason = "Trailing SL"
                elif duration_min >= stagnation_min and abs(pnl_pct) <= 0.004:
                    should_exit = True
                    exit_reason = f"Stagnation ({int(duration_min)}m)"

            elif direction == "SHORT":
                if ltp < pos["lowest_price"]:
                    pos["lowest_price"] = ltp

                pnl_pct = (entry_price - ltp) / entry_price
                max_fav = (entry_price - pos["lowest_price"]) / entry_price

                if pnl_pct >= target_pct:
                    should_exit = True
                    exit_reason = f"Target {target_pct*100:.1f}%"
                elif pnl_pct <= -sl_pct:
                    should_exit = True
                    exit_reason = f"Stop Loss -{sl_pct*100:.1f}%"
                elif max_fav >= 0.005 and ((ltp - pos["lowest_price"]) / pos["lowest_price"]) >= 0.005:
                    should_exit = True
                    exit_reason = "Trailing SL"
                elif duration_min >= stagnation_min and abs(pnl_pct) <= 0.004:
                    should_exit = True
                    exit_reason = f"Stagnation ({int(duration_min)}m)"

            if should_exit:
                pnl = (ltp - entry_price) * qty if direction == "LONG" else (entry_price - ltp) * qty
                ret_pct = ((ltp - entry_price) / entry_price * 100) if direction == "LONG" else ((entry_price - ltp) / entry_price * 100)
                completed_trades.append({
                    "symbol": sym,
                    "direction": direction,
                    "entry_time": pos["entry_time_str"],
                    "exit_time": t_str,
                    "entry_price": entry_price,
                    "exit_price": ltp,
                    "qty": qty,
                    "pnl": round(pnl, 2),
                    "return_pct": f"{ret_pct:+.2f}%",
                    "reason": exit_reason,
                })
                to_close.append(sym)

        for s in to_close:
            del open_positions[s]

        # 2. Check entries
        if can_enter and len(open_positions) < max_positions:
            for sym, bars in day_data.items():
                if len(open_positions) >= max_positions:
                    break
                if sym in open_positions:
                    continue

                strat = config.get(sym)
                if not strat:
                    continue

                bar = next((b for b in bars if b["time_str"] == t_str), None)
                if not bar:
                    continue

                ltp = bar["close"]
                hl = high_low.get(sym)
                if not hl:
                    continue

                # Directive check
                directive = "NEUTRAL"
                if directives and sym in directives:
                    directive = directives[sym]

                if directive == "BLOCKED":
                    continue

                allow_long_for_sym = allow_longs and (directive != "SHORT_ONLY")
                allow_short_for_sym = allow_shorts and strat.get("allow_short", True) and (directive != "LONG_ONLY")

                buf_long = strat.get("breakout_long", 0.003) * breakout_mult
                buf_short = strat.get("breakout_short", 0.003) * breakout_mult
                lev = strat.get("leverage", 1.0)
                eff_budget = budget * lev

                # Check Long
                if allow_long_for_sym and hl["high"] > 0 and ltp >= hl["high"] * (1.0 + buf_long):
                    qty = int(eff_budget / ltp)
                    if qty >= 1:
                        open_positions[sym] = {
                            "symbol": sym,
                            "direction": "LONG",
                            "entry_price": ltp,
                            "entry_time": bar["timestamp"],
                            "entry_time_str": t_str,
                            "qty": qty,
                            "highest_price": ltp,
                        }
                        continue

                # Check Short
                if allow_short_for_sym and hl["low"] > 0 and ltp <= hl["low"] * (1.0 - buf_short):
                    qty = int(eff_budget / ltp)
                    if qty >= 1:
                        open_positions[sym] = {
                            "symbol": sym,
                            "direction": "SHORT",
                            "entry_price": ltp,
                            "entry_time": bar["timestamp"],
                            "entry_time_str": t_str,
                            "qty": qty,
                            "lowest_price": ltp,
                        }
                        continue

        # 3. Update established High/Low
        for sym, bars in day_data.items():
            bar = next((b for b in bars if b["time_str"] == t_str), None)
            if not bar:
                continue
            if sym not in high_low:
                high_low[sym] = {"high": bar["high"], "low": bar["low"]}
            else:
                if bar["high"] > high_low[sym]["high"]:
                    high_low[sym]["high"] = bar["high"]
                if bar["low"] < high_low[sym]["low"]:
                    high_low[sym]["low"] = bar["low"]

    return completed_trades


def summarize_trades(trades, label):
    pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    print(f"\n--- {label} ---")
    print(f"Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {win_rate:.1f}% | Net P&L: Rs.{pnl:+,.2f}")
    if trades:
        for t in trades:
            pnl_s = f"+Rs.{t['pnl']:.2f}" if t['pnl'] > 0 else f"-Rs.{abs(t['pnl']):.2f}"
            print(f"  [{t['entry_time']} -> {t['exit_time']}] {t['direction']:<5} {t['symbol']:<10} Qty:{t['qty']:<3} @ Rs.{t['entry_price']:.1f} -> Rs.{t['exit_price']:.1f} | {pnl_s:<10} ({t['return_pct']:<7}) | {t['reason']}")
    return pnl, len(trades), len(wins), len(losses)


def run_full_audit_for_date(all_data, config, sector_matrix, target_date, directives):
    print(f"\n#################################################################")
    print(f"               FULL SIMULATION AUDIT FOR {target_date}           ")
    print(f"#################################################################")

    # 1. Market reality
    stock_perf, sector_perf = analyze_market_reality(all_data, target_date, sector_matrix)

    # 2. Simulate Modes
    # Mode 1: Live Bot (Sentinel Directed: 09:30 filter, 1 position, Rs.10k in Caution)
    trades_sentinel = run_strategy_simulation(
        all_data, config, target_date,
        directives=directives,
        start_time_min=30,
        max_positions=1,
        budget=10000.0,
        stagnation_min=45,
    )

    # Mode 2: Normal Unconstrained (09:30 filter, Long+Short, 3 positions, Rs.15k, no directives)
    trades_normal = run_strategy_simulation(
        all_data, config, target_date,
        directives=None,
        start_time_min=30,
        max_positions=3,
        budget=15000.0,
        stagnation_min=45,
    )

    # Mode 3: Aggressive Mode (09:15 early entry, 0.1% buffer, 5 positions, Rs.20k, no directives)
    trades_aggressive = run_strategy_simulation(
        all_data, config, target_date,
        directives=None,
        start_time_min=15,
        breakout_mult=0.33,
        max_positions=5,
        budget=20000.0,
        stagnation_min=25,
    )

    # Mode 4: Aggressive Directional (09:15 early entry, 0.1% buffer, 3 positions, Sentinel directives)
    trades_agg_dir = run_strategy_simulation(
        all_data, config, target_date,
        directives=directives,
        start_time_min=15,
        breakout_mult=0.33,
        max_positions=3,
        budget=15000.0,
        stagnation_min=25,
    )

    p1, t1, w1, l1 = summarize_trades(trades_sentinel, "SCENARIO 1: LIVE BOT (Sentinel Directed, 09:30 Filter, Caution Sizing)")
    p2, t2, w2, l2 = summarize_trades(trades_normal, "SCENARIO 2: NORMAL UNCONSTRAINED (09:30 Filter, Dual Direction, 3 Pos)")
    p3, t3, w3, l3 = summarize_trades(trades_aggressive, "SCENARIO 3: AGGRESSIVE (09:15 Entry, 0.1% Buffer, 5 Pos, Fast Stagnation)")
    p4, t4, w4, l4 = summarize_trades(trades_agg_dir, "SCENARIO 4: AGGRESSIVE DIRECTIONAL (09:15 Entry, 0.1% Buffer, Sentinel Directives)")

    print(f"\n=================================================================")
    print(f"              SCENARIO COMPARISON MATRIX ({target_date})        ")
    print(f"=================================================================")
    print(f"{'STRATEGY SCENARIO':<32} | {'TRADES':<6} | {'W/L':<8} | {'NET P&L':<12}")
    print("-" * 65)
    print(f"{'1. Live Bot (Sentinel Directed)':<32} | {t1:<6} | {w1}W/{l1}L    | Rs.{p1:+8.2f}")
    print(f"{'2. Normal Unconstrained':<32} | {t2:<6} | {w2}W/{l2}L    | Rs.{p2:+8.2f}")
    print(f"{'3. Aggressive (09:15 + 0.1% buf)':<32} | {t3:<6} | {w3}W/{l3}L    | Rs.{p3:+8.2f}")
    print(f"{'4. Aggressive Directional':<32} | {t4:<6} | {w4}W/{l4}L    | Rs.{p4:+8.2f}")
    print("=" * 65)

    return {
        "stock_perf": stock_perf,
        "sector_perf": sector_perf,
        "scenarios": {
            "sentinel": {"pnl": p1, "trades": t1, "w": w1, "l": l1},
            "normal": {"pnl": p2, "trades": t2, "w": w2, "l": l2},
            "aggressive": {"pnl": p3, "trades": t3, "w": w3, "l": l3},
            "agg_directional": {"pnl": p4, "trades": t4, "w": w4, "l": l4},
        }
    }


def main():
    stocks_json = json.loads(STOCKS_FILE.read_text(encoding="utf-8"))
    config_json = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    sector_matrix = json.loads(SECTOR_MATRIX_FILE.read_text(encoding="utf-8"))
    tickers = stocks_json.get("tickers", [])

    all_data = fetch_all_intraday_data(tickers)

    # Active directives from Sentinel regime
    directives = {
        # Beneficiaries (Long Only)
        "HAL": "LONG_ONLY", "BEL": "LONG_ONLY", "MAZDOCK": "LONG_ONLY", "COCHINSHIP": "LONG_ONLY",
        "TATAPOWER": "LONG_ONLY", "TORNTPOWER": "LONG_ONLY", "BHEL": "LONG_ONLY", "MUTHOOTFIN": "LONG_ONLY",
        # Victims (Short Only)
        "TVSMOTOR": "SHORT_ONLY", "ASHOKLEY": "SHORT_ONLY", "ADANIPORTS": "SHORT_ONLY", "VOLTAS": "SHORT_ONLY", "TRENT": "SHORT_ONLY",
    }

    # Audit Yesterday (2026-09-22)
    res_sep22 = run_full_audit_for_date(all_data, config_json, sector_matrix, "2026-09-22", directives)

    # Audit Today (2026-09-23)
    res_sep23 = run_full_audit_for_date(all_data, config_json, sector_matrix, "2026-09-23", directives)


if __name__ == "__main__":
    main()
