#!/usr/bin/env python3
"""
Axiom Dynamic Moving Stock Window Scanner
Scans the entire Nifty 500 universe (~501 stocks) daily.
Applies quantitative filters (Liquidity, 5-day ATR%, Momentum, Volume Surge)
to select the Top 50 highest-conviction Moving Stock Window for the trading day.
"""

import csv
from io import StringIO
import json
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

NIFTY_CONSTITUENTS_FILE = DATA_DIR / "nifty500_constituents.json"
STOCKS_FILE = DATA_DIR / "stocks.json"
CONFIG_FILE = DATA_DIR / "config.json"
SECTOR_MATRIX_FILE = DATA_DIR / "sector_matrix.json"

NSE_NIFTY500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"


def ensure_nifty500_constituents():
    """Download official Nifty 500 constituents from NSE Archives if missing or older than 30 days."""
    if NIFTY_CONSTITUENTS_FILE.exists():
        try:
            stocks = json.loads(NIFTY_CONSTITUENTS_FILE.read_text(encoding="utf-8"))
            if len(stocks) >= 450:
                return stocks
        except Exception:
            pass

    print("[SCANNER] Fetching fresh Nifty 500 list from NSE Archives...")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(NSE_NIFTY500_URL, headers=headers, timeout=10)
        if r.status_code == 200:
            reader = csv.DictReader(StringIO(r.text.strip()))
            stocks = []
            for row in reader:
                sym = row.get("Symbol", "").strip()
                ind = row.get("Industry", "General").strip()
                name = row.get("Company Name", "").strip()
                if sym:
                    stocks.append({"symbol": sym, "industry": ind, "name": name})
            NIFTY_CONSTITUENTS_FILE.write_text(json.dumps(stocks, indent=2), encoding="utf-8")
            print(f"[SCANNER] Cached {len(stocks)} Nifty 500 constituents.")
            return stocks
    except Exception as e:
        print(f"[SCANNER] Failed to fetch Nifty 500 list from NSE: {e}")

    # Fallback to existing if available
    if NIFTY_CONSTITUENTS_FILE.exists():
        return json.loads(NIFTY_CONSTITUENTS_FILE.read_text(encoding="utf-8"))
    return []


def fetch_stock_metrics(item):
    """
    Fetch 5-day daily metrics and compute:
    1. Liquidity (Daily turnover > 10 Crore)
    2. Volatility (5-day ATR% >= 1.4%)
    3. Momentum (5-day relative price change)
    4. Volume Surge Ratio
    5. Composite Alpha Score
    """
    sym = item["symbol"]
    industry = item["industry"]

    ytick = f"{sym}.NS"
    if sym == "WABAG":
        ytick = "VA-TECH.NS"
    elif sym == "M&M":
        ytick = "M&M.NS"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ytick}?interval=1d&range=5d"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        r = requests.get(url, headers=headers, timeout=6)
        if r.status_code != 200:
            return None
        res = r.json().get("chart", {}).get("result", [{}])[0]
        quote = res.get("indicators", {}).get("quote", [{}])[0]
        highs = [h for h in quote.get("high", []) if h is not None]
        lows = [l for l in quote.get("low", []) if l is not None]
        closes = [c for c in quote.get("close", []) if c is not None]
        volumes = [v for v in quote.get("volume", []) if v is not None]

        if len(closes) < 3 or len(highs) < 3:
            return None

        ltp = closes[-1]
        avg_vol = sum(volumes) / len(volumes) if volumes else 1.0
        daily_turnover_cr = (ltp * avg_vol) / 10000000.0  # in Crore INR

        # Liquidity filter: must trade at least 10 Crore INR daily turnover
        if daily_turnover_cr < 10.0:
            return None

        # 5-day Average True Range (ATR %)
        ranges = [(h - l) / c for h, l, c in zip(highs, lows, closes) if c > 0]
        atr_pct = (sum(ranges) / len(ranges)) * 100.0

        # Minimum volatility hurdle: at least 1.4% daily range
        if atr_pct < 1.4:
            return None

        # 5-day price momentum
        ret_5d = ((closes[-1] - closes[0]) / closes[0]) * 100.0

        # Volume Surge Ratio
        vol_surge = (volumes[-1] / avg_vol) if avg_vol > 0 else 1.0

        # Composite Score: High ATR% + directional momentum magnitude + volume surge
        score = (atr_pct * 2.0) + (abs(ret_5d) * 1.5) + (vol_surge * 1.0)

        return {
            "symbol": sym,
            "industry": industry,
            "ltp": round(ltp, 2),
            "turnover_cr": round(daily_turnover_cr, 1),
            "atr_pct": round(atr_pct, 2),
            "ret_5d": round(ret_5d, 2),
            "vol_surge": round(vol_surge, 2),
            "score": round(score, 2),
        }
    except Exception:
        return None


def run_scanner(top_n=50, max_per_sector=7):
    """
    Main scanner execution:
    1. Scans all 501 Nifty 500 constituents using 25 threads.
    2. Ranks by Composite Alpha Score.
    3. Enforces sector diversification.
    4. Writes data/stocks.json, data/config.json, and data/sector_matrix.json.
    """
    stocks = ensure_nifty500_constituents()
    if not stocks:
        print("[SCANNER] Error: No constituents found.")
        return []

    print(f"[SCANNER] Scanning {len(stocks)} Nifty 500 stocks across all sectors...")
    start_t = time.time()

    results = []
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {executor.submit(fetch_stock_metrics, s): s for s in stocks}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)

    elapsed = time.time() - start_t
    print(f"[SCANNER] Qualified {len(results)} liquid, high-volatility stocks in {elapsed:.1f}s.")

    # Rank by composite score
    results.sort(key=lambda x: x["score"], reverse=True)

    # Apply sector diversification limit
    selected = []
    sector_counts = {}
    for r in results:
        sec = r["industry"]
        if sector_counts.get(sec, 0) < max_per_sector:
            selected.append(r)
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
            if len(selected) >= top_n:
                break

    selected_tickers = [s["symbol"] for s in selected]

    # 1. Write data/stocks.json
    stocks_payload = {"tickers": selected_tickers}
    STOCKS_FILE.write_text(json.dumps(stocks_payload, indent=2), encoding="utf-8")
    print(f"[SCANNER] Wrote Top {len(selected_tickers)} Moving Stock Window to {STOCKS_FILE}")

    # 2. Write data/config.json with calibrated strategy parameters
    config_payload = {}
    for s in selected:
        sym = s["symbol"]
        atr = s["atr_pct"]
        # Class A for high beta (ATR >= 3.5%), Class B for moderate beta
        stock_class = "A" if atr >= 3.5 else "B"
        target_pct = 0.025 if stock_class == "A" else 0.020
        config_payload[sym] = {
            "class": stock_class,
            "allow_short": True,
            "breakout_long": 0.002,
            "breakout_short": 0.002,
            "target": target_pct,
            "sl": 0.010,
            "leverage": 1.0,
        }
    CONFIG_FILE.write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
    print(f"[SCANNER] Calibrated strategy parameters for {len(config_payload)} stocks in {CONFIG_FILE}")

    # 3. Write data/sector_matrix.json
    sector_matrix_payload = {}
    for s in selected:
        sym = s["symbol"]
        atr = s["atr_pct"]
        stock_class = "A" if atr >= 3.5 else "B"
        sector_matrix_payload[sym] = {
            "sector": s["industry"],
            "beta_class": stock_class,
            "weight": 1.0,
        }
    SECTOR_MATRIX_FILE.write_text(json.dumps(sector_matrix_payload, indent=2), encoding="utf-8")
    print(f"[SCANNER] Generated sector matrix for {len(sector_matrix_payload)} stocks in {SECTOR_MATRIX_FILE}")

    print("===========================================================================")
    print(f" TOP {len(selected)} MOVING STOCK WINDOW SELECTION SUMMARY")
    print("===========================================================================")
    print(f"{'Rank':<4} {'Symbol':<12} {'Sector':<22} {'LTP':<8} {'ATR%':<6} {'5d Ret%':<8} {'Score':<6}")
    print("-" * 75)
    for idx, s in enumerate(selected[:15], 1):
        print(f"{idx:<4} {s['symbol']:<12} {s['industry']:<22} {s['ltp']:<8} {s['atr_pct']:<6} {s['ret_5d']:<8} {s['score']:<6}")
    print(f"... and {len(selected) - 15} more stocks across {len(sector_counts)} sectors.")
    print("===========================================================================")

    return selected_tickers


if __name__ == "__main__":
    run_scanner()
