import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

IST = timezone(timedelta(hours=5, minutes=30))
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = DATA_DIR / "intraday_30d_cache.json"

# Load stocks from stocks.json and add base market leaders
stocks_data = json.loads(Path("data/stocks.json").read_text())
tickers = set(stocks_data["tickers"])

# Add base high-momentum sector names
BASE_NAMES = [
    "TRENT", "DIXON", "POLYCAB", "KEI", "KAYNES", "WABAG", "HAL", "BEL",
    "MAZDOCK", "COCHINSHIP", "RVNL", "IRFC", "TITAGARH", "IREDA", "SUZLON",
    "TATAPOWER", "TORNTPOWER", "BHEL", "ADANIPORTS", "DLF", "GODREJPROP",
    "CHOLAFIN", "SHRIRAMFIN", "MUTHOOTFIN", "FEDERALBNK", "BANKINDIA",
    "VOLTAS", "TVSMOTOR", "ASHOKLEY", "JINDALSTEL", "HINDALCO", "TATASTEEL",
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "LT",
    "BHARTIARTL", "ITC", "AXISBANK", "KOTAKBANK", "MARUTI", "SUNPHARMA",
    "JSWSTEEL", "COALINDIA", "NTPC", "ONGC", "POWERGRID", "BAJFINANCE"
]
tickers.update(BASE_NAMES)
all_symbols = sorted(list(tickers))

def fetch_symbol_bars(sym):
    ytick = f"{sym}.NS"
    if sym == "WABAG":
        ytick = "VA-TECH.NS"
    elif sym == "M&M":
        ytick = "M&M.NS"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ytick}?interval=2m&range=60d"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return sym, []
        res = r.json().get("chart", {}).get("result", [{}])[0]
        timestamps = res.get("timestamp", [])
        quote = res.get("indicators", {}).get("quote", [{}])[0]
        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        bars = []
        for i, ts in enumerate(timestamps):
            if i >= len(opens) or opens[i] is None or closes[i] is None:
                continue
            dt = datetime.fromtimestamp(ts, tz=IST)
            h, m = dt.hour, dt.minute
            # Market hours: 09:15 to 15:30 IST
            if not ((h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)):
                continue

            vol = volumes[i] if (i < len(volumes) and volumes[i] is not None) else 1
            bars.append({
                "timestamp": ts,
                "date": dt.strftime("%Y-%m-%d"),
                "time_str": dt.strftime("%H:%M"),
                "open": round(opens[i], 2),
                "high": round(highs[i], 2),
                "low": round(lows[i], 2),
                "close": round(closes[i], 2),
                "volume": int(vol),
            })
        return sym, bars
    except Exception:
        return sym, []

def main():
    print(f"Downloading 30+ trading days of 2-minute intraday bars for {len(all_symbols)} tickers...")
    start_t = time.time()
    all_data = {}
    success_count = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_symbol_bars, sym): sym for sym in all_symbols}
        for future in as_completed(futures):
            sym, bars = future.result()
            if bars:
                all_data[sym] = bars
                success_count += 1

    elapsed = time.time() - start_t
    print(f"Successfully downloaded {success_count}/{len(all_symbols)} tickers in {elapsed:.1f}s.")

    CACHE_FILE.write_text(json.dumps(all_data), encoding="utf-8")
    print(f"Saved {CACHE_FILE} ({CACHE_FILE.stat().st_size / (1024*1024):.1f} MB)")

    dates = sorted(list(set(b['date'] for bars in all_data.values() for b in bars)))
    print(f"Total unique trading days captured: {len(dates)}")
    print(f"Date range: {dates[0]} to {dates[-1]}")

if __name__ == "__main__":
    main()
