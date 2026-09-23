import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

IST = timezone(timedelta(hours=5, minutes=30))
DATA_DIR = Path('data')
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = DATA_DIR / 'intraday_23d_cache.json'

# Base 32 watchlist + Top Nifty 50/100 liquid sector leaders
EXPANDED_TICKERS = [
    # Axiom Base 32 Watchlist
    "TRENT", "DIXON", "POLYCAB", "KEI", "KAYNES", "WABAG", "HAL", "BEL",
    "MAZDOCK", "COCHINSHIP", "RVNL", "IRFC", "TITAGARH", "IREDA", "SUZLON",
    "TATAPOWER", "TORNTPOWER", "BHEL", "ADANIPORTS", "DLF", "GODREJPROP",
    "CHOLAFIN", "SHRIRAMFIN", "MUTHOOTFIN", "FEDERALBNK", "BANKINDIA",
    "VOLTAS", "TVSMOTOR", "ASHOKLEY", "JINDALSTEL", "HINDALCO", "TATASTEEL",
    # Top Liquid Market Leaders for Dynamic Scanning
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "LT",
    "BHARTIARTL", "ITC", "AXISBANK", "KOTAKBANK", "MARUTI", "SUNPHARMA",
    "JSWSTEEL", "COALINDIA", "NTPC", "ONGC", "POWERGRID", "BAJFINANCE"
]

def download_universe():
    print(f"Downloading 1-month (23 trading days) 2-minute bars for {len(EXPANDED_TICKERS)} symbols...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }

    all_data = {}
    success = 0

    for idx, s in enumerate(EXPANDED_TICKERS):
        ytick = "VA-TECH.NS" if s == "WABAG" else f"{s}.NS"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ytick}?interval=2m&range=1mo"

        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                res = r.json().get("chart", {}).get("result", [{}])[0]
                timestamps = res.get("timestamp", [])
                quote = res.get("indicators", {}).get("quote", [{}])[0]
                opens = quote.get("open", [])
                highs = quote.get("high", [])
                lows = quote.get("low", [])
                closes = quote.get("close", [])
                vols = quote.get("volume", [])

                bars = []
                for i, ts in enumerate(timestamps):
                    if i >= len(opens) or opens[i] is None or closes[i] is None:
                        continue
                    dt = datetime.fromtimestamp(ts, tz=IST)
                    h, m = dt.hour, dt.minute
                    # Market hours: 09:15 to 15:30
                    if not ((h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)):
                        continue

                    bars.append({
                        "timestamp": ts,
                        "time_str": dt.strftime("%H:%M"),
                        "date": dt.strftime("%Y-%m-%d"),
                        "open": round(opens[i], 2),
                        "high": round(highs[i], 2),
                        "low": round(lows[i], 2),
                        "close": round(closes[i], 2),
                        "volume": vols[i] if vols[i] is not None else 1
                    })

                if bars:
                    all_data[s] = bars
                    success += 1
                    dates_count = len(set(b['date'] for b in bars))
                    print(f"[{idx+1}/{len(EXPANDED_TICKERS)}] {s:<12}: {len(bars)} bars across {dates_count} days")
            else:
                print(f"[{idx+1}/{len(EXPANDED_TICKERS)}] {s:<12}: HTTP {r.status_code}")
        except Exception as e:
            print(f"[{idx+1}/{len(EXPANDED_TICKERS)}] {s:<12}: Error {e}")

        time.sleep(0.3)

    CACHE_FILE.write_text(json.dumps(all_data), encoding="utf-8")
    print(f"\nSaved {success} symbols to {CACHE_FILE} ({CACHE_FILE.stat().st_size / 1024 / 1024:.2f} MB)")

if __name__ == '__main__':
    download_universe()
