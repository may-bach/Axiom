#!/usr/bin/env python3
"""
Axiom Protocol - Historical Simulation Engine for Today (September 21, 2026)
Simulates what would have happened if the bot traded today without shutting down.
Compares:
  Scenario 1: Normal Mode (Longs + Shorts allowed)
  Scenario 2: Bearish Crisis Mode (Shorts Only allowed)
"""

from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import time
import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DATA_DIR = Path("data")
STOCKS_FILE = DATA_DIR / "stocks.json"
CONFIG_FILE = DATA_DIR / "config.json"

INITIAL_CAPITAL = 10000.00
MAX_POSITIONS = 3
TRAILING_BUFFER = 0.005 # 0.5%
STAGNATION_MINUTES = 45


def get_yahoo_data(symbol):
    """Fetches today's 1-minute intraday bars from Yahoo Finance."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }
    # Yahoo ticker format
    yticker = f"{symbol}.NS"
    if symbol == "WABAG":
        yticker = "VA-TECH.NS"
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yticker}?interval=1m&range=1d"
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            if symbol == "WABAG":
                # try alternative
                r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/WABAG.NS?interval=1m&range=1d", headers=headers, timeout=8)
                if r.status_code != 200:
                    return None
            else:
                return None

        data = r.json()
        result = data.get("chart", {}).get("result", [{}])[0]
        timestamps = result.get("timestamp", [])
        if not timestamps:
            return None

        quote = result.get("indicators", {}).get("quote", [{}])[0]
        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])

        bars = []
        for i in range(len(timestamps)):
            ts = timestamps[i]
            dt = datetime.fromtimestamp(ts)
            # IST filter: 09:15 to 15:30
            # Note: timestamps from Yahoo are UTC, dt is local or UTC
            # Let's convert to IST string HH:MM
            h, m = dt.hour, dt.minute
            
            c = closes[i]
            if c is None:
                continue
            o = opens[i] if opens[i] is not None else c
            h_val = highs[i] if highs[i] is not None else max(o, c)
            l_val = lows[i] if lows[i] is not None else min(o, c)

            bars.append({
                "timestamp": ts,
                "datetime": dt,
                "time_str": dt.strftime("%H:%M"),
                "open": float(o),
                "high": float(h_val),
                "low": float(l_val),
                "close": float(c),
            })
        return bars
    except Exception as e:
        return None


def run_simulation(stocks_data, config, allow_longs=True, allow_shorts=True):
    """
    Simulates minute-by-minute execution of Axiom Breakout & Trailing SL Engine.
    """
    # 1. Collect all unique time buckets across symbols
    all_times = sorted(list(set(b["time_str"] for s in stocks_data.values() for b in s)))
    
    # 2. Tracking state
    high_low = {} # sym -> {high, low}
    open_positions = {} # sym -> pos dict
    completed_trades = []
    
    # Position sizing: baseline 10k * 1.5 * leverage (under 5x margin)
    base_budget = INITIAL_CAPITAL * 1.5

    for t_str in all_times:
        hour, minute = int(t_str.split(":")[0]), int(t_str.split(":")[1])
        
        # Market open check (NSE hours: 09:15 to 15:30)
        is_market_open = (hour == 9 and minute >= 15) or (hour > 9 and hour < 15) or (hour == 15 and minute <= 30)
        can_enter = (hour == 9 and minute >= 30) or (hour > 9 and hour < 15)
        is_eod_squareoff = (hour == 15 and minute >= 10)

        # 1. Update exits for open positions
        to_close = []
        for sym, pos in list(open_positions.items()):
            # Find current bar for symbol
            bar = next((b for b in stocks_data[sym] if b["time_str"] == t_str), None)
            if not bar:
                continue

            ltp = bar["close"]
            strat = config.get(sym, {})
            target_pct = strat.get("target", 0.025)
            sl_pct = strat.get("sl", 0.012)
            entry_price = pos["entry_price"]
            direction = pos["direction"]
            qty = pos["qty"]
            duration_minutes = (bar["timestamp"] - pos["entry_time"]) / 60.0

            should_exit = False
            exit_reason = ""

            # Check EOD square-off
            if is_eod_squareoff:
                should_exit = True
                exit_reason = "EOD Square-off"
            elif direction == "LONG":
                # Update highest price seen
                if ltp > pos["highest_price"]:
                    pos["highest_price"] = ltp

                pnl_pct = (ltp - entry_price) / entry_price
                max_favorable_pct = (pos["highest_price"] - entry_price) / entry_price

                # A. Target check
                if pnl_pct >= target_pct:
                    should_exit = True
                    exit_reason = f"Target {target_pct*100:.1f}%"
                # B. Stop Loss check
                elif pnl_pct <= -sl_pct:
                    should_exit = True
                    exit_reason = f"Stop Loss -{sl_pct*100:.1f}%"
                # C. Trailing Stop Loss
                elif max_favorable_pct >= TRAILING_BUFFER:
                    pullback_from_peak = (pos["highest_price"] - ltp) / pos["highest_price"]
                    if pullback_from_peak >= TRAILING_BUFFER:
                        should_exit = True
                        exit_reason = "Trailing SL"
                # D. Stagnation Timeout
                elif duration_minutes >= STAGNATION_MINUTES and pnl_pct < 0.005:
                    should_exit = True
                    exit_reason = f"Stagnation ({int(duration_minutes)}m)"

            elif direction == "SHORT":
                # Update lowest price seen
                if ltp < pos["lowest_price"]:
                    pos["lowest_price"] = ltp

                pnl_pct = (entry_price - ltp) / entry_price
                max_favorable_pct = (entry_price - pos["lowest_price"]) / entry_price

                # A. Target check
                if pnl_pct >= target_pct:
                    should_exit = True
                    exit_reason = f"Target {target_pct*100:.1f}%"
                # B. Stop Loss check
                elif pnl_pct <= -sl_pct:
                    should_exit = True
                    exit_reason = f"Stop Loss -{sl_pct*100:.1f}%"
                # C. Trailing Stop Loss
                elif max_favorable_pct >= TRAILING_BUFFER:
                    pullback_from_low = (ltp - pos["lowest_price"]) / pos["lowest_price"]
                    if pullback_from_low >= TRAILING_BUFFER:
                        should_exit = True
                        exit_reason = "Trailing SL"
                # D. Stagnation Timeout
                elif duration_minutes >= STAGNATION_MINUTES and pnl_pct < 0.005:
                    should_exit = True
                    exit_reason = f"Stagnation ({int(duration_minutes)}m)"

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

        for sym in to_close:
            del open_positions[sym]

        # 2. Check entries for available stocks if slots open
        if can_enter and len(open_positions) < MAX_POSITIONS:
            for sym, bars in stocks_data.items():
                if len(open_positions) >= MAX_POSITIONS:
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

                # High/Low check
                if sym not in high_low:
                    high_low[sym] = {"high": bar["high"], "low": bar["low"]}
                    continue

                hl = high_low[sym]
                breakout_long = strat.get("breakout_long", 0.003)
                breakout_short = strat.get("breakout_short", 0.003)
                can_short = strat.get("allow_short", False)
                leverage = strat.get("leverage", 1.0)
                eff_budget = base_budget * leverage

                # Evaluate LONG
                if allow_longs and ltp >= hl["high"] * (1.0 + breakout_long):
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

                # Evaluate SHORT
                if allow_shorts and can_short and ltp <= hl["low"] * (1.0 - breakout_short):
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

        # 3. Update High / Low after evaluating entries
        for sym, bars in stocks_data.items():
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


def main():
    print("=" * 65)
    print("   AXIOM HISTORICAL SIMULATION ENGINE — TODAY (2026-09-21)   ")
    print("=" * 65)

    stocks_json = json.loads(STOCKS_FILE.read_text(encoding="utf-8"))
    config_json = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    tickers = stocks_json.get("tickers", [])

    print(f"Downloading 1-minute intraday bars for {len(tickers)} stocks...")
    stocks_data = {}
    for sym in tickers:
        bars = get_yahoo_data(sym)
        if bars and len(bars) > 10:
            stocks_data[sym] = bars
        time.sleep(0.05)

    print(f"Successfully retrieved intraday bars for {len(stocks_data)}/{len(tickers)} stocks.\n")

    # Run Scenario 1: Normal Trading (Long + Short)
    trades_normal = run_simulation(stocks_data, config_json, allow_longs=True, allow_shorts=True)
    pnl_normal = sum(t["pnl"] for t in trades_normal)
    wins_normal = [t for t in trades_normal if t["pnl"] > 0]
    losses_normal = [t for t in trades_normal if t["pnl"] < 0]

    # Run Scenario 2: Bearish Crisis Mode (Short Breakdown Only)
    trades_short_only = run_simulation(stocks_data, config_json, allow_longs=False, allow_shorts=True)
    pnl_short_only = sum(t["pnl"] for t in trades_short_only)
    wins_short_only = [t for t in trades_short_only if t["pnl"] > 0]
    losses_short_only = [t for t in trades_short_only if t["pnl"] < 0]

    # Output Scenario 1
    print("-----------------------------------------------------------------")
    print("SCENARIO 1: NORMAL MODE (Longs + Shorts Allowed)")
    print("-----------------------------------------------------------------")
    print(f"Total Completed Trades : {len(trades_normal)}")
    print(f"Win / Loss Record      : {len(wins_normal)}W / {len(losses_normal)}L")
    print(f"Net Realized P&L       : Rs.{pnl_normal:+,.2f} ({(pnl_normal/INITIAL_CAPITAL)*100:+.2f}%)")
    print(f"Ending Capital         : Rs.{INITIAL_CAPITAL + pnl_normal:,.2f}")
    if trades_normal:
        print("\nExecuted Trades:")
        for t in trades_normal:
            pnl_str = f"+Rs.{t['pnl']:.2f}" if t['pnl'] > 0 else f"-Rs.{abs(t['pnl']):.2f}"
            print(f"  [{t['entry_time']} -> {t['exit_time']}] {t['direction']:<5} {t['symbol']:<10} Qty:{t['qty']:<4} @ Rs.{t['entry_price']:.1f} -> Rs.{t['exit_price']:.1f} | P&L: {pnl_str:<10} ({t['return_pct']:<7}) | {t['reason']}")

    # Output Scenario 2
    print("\n-----------------------------------------------------------------")
    print("SCENARIO 2: BEARISH CRISIS MODE (Shorts Only — No Longs)")
    print("-----------------------------------------------------------------")
    print(f"Total Completed Trades : {len(trades_short_only)}")
    print(f"Win / Loss Record      : {len(wins_short_only)}W / {len(losses_short_only)}L")
    print(f"Net Realized P&L       : Rs.{pnl_short_only:+,.2f} ({(pnl_short_only/INITIAL_CAPITAL)*100:+.2f}%)")
    print(f"Ending Capital         : Rs.{INITIAL_CAPITAL + pnl_short_only:,.2f}")
    if trades_short_only:
        print("\nExecuted Trades:")
        for t in trades_short_only:
            pnl_str = f"+Rs.{t['pnl']:.2f}" if t['pnl'] > 0 else f"-Rs.{abs(t['pnl']):.2f}"
            print(f"  [{t['entry_time']} -> {t['exit_time']}] {t['direction']:<5} {t['symbol']:<10} Qty:{t['qty']:<4} @ Rs.{t['entry_price']:.1f} -> Rs.{t['exit_price']:.1f} | P&L: {pnl_str:<10} ({t['return_pct']:<7}) | {t['reason']}")

    print("=" * 65)


if __name__ == "__main__":
    main()
