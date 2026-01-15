# brain.py
import os
import json
import time
import pyotp
from datetime import datetime, timedelta
from dotenv import load_dotenv
from SmartApi import SmartConnect


# ----------------------------------------------------------------------
# Load env
# ----------------------------------------------------------------------
load_dotenv()

CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE")
PASSWORD    = os.getenv("ANGEL_PASSWORD")
API_KEY     = os.getenv("ANGEL_API_KEY")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")

if not all([CLIENT_CODE, PASSWORD, API_KEY, TOTP_SECRET]):
    raise ValueError("Missing Angel One credentials in .env")

# ----------------------------------------------------------------------
# Restricted list (no shorting)
# ----------------------------------------------------------------------
RESTRICTED_LIST = [
    "BEML", "MAZDOCK", "BDL", "PARAS", "COCHINSHIP", "GRSE",
    "ADANIPORTS", "ADANIENT", "IRCON", "HAL", "RAILTEL"
]

# ----------------------------------------------------------------------
# Load watch-list
# ----------------------------------------------------------------------
try:
    with open("stocks.json", "r") as f:
        STOCKS = json.load(f)["tickers"]
    print(f"Loaded {len(STOCKS)} stocks from stocks.json")
except Exception as e:
    print(f"-----")

# ----------------------------------------------------------------------
# Angel One login
# ----------------------------------------------------------------------
def angel_login():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_CODE, PASSWORD, totp)
    if data["status"]:
        print("Angel One login successful")
        return obj
    raise Exception(f"Angel login failed: {data.get('message')}")

api = angel_login()

# ----------------------------------------------------------------------
# Helper: fetch historic daily data (last ~90 days)
# ----------------------------------------------------------------------
def fetch_historic(symbol):
    to_date   = datetime.now()
    from_date = to_date - timedelta(days=95)
    payload = {
        "exchange": "NSE",
        "symboltoken": get_token(symbol),
        "interval": "ONE_DAY",
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate"  : to_date.strftime("%Y-%m-%d %H:%M")
    }
    resp = api.getCandleData(payload)
    if resp["status"] and resp["data"]:
        return resp["data"]
    print(f"No historic data for {symbol}")
    return []

# ----------------------------------------------------------------------
# Token lookup (Angel One token for a symbol)
# ----------------------------------------------------------------------
def get_token(symbol):
    # Cache to avoid repeated calls
    if hasattr(get_token, "cache"):
        if symbol in get_token.cache:
            return get_token.cache[symbol]
    else:
        get_token.cache = {}

    # Search scrip
    search = api.searchScrip(exchange="NSE", searchscrip=symbol)
    if search["status"]:
        for item in search["data"]:
            if item["tradingsymbol"] == f"{symbol}-EQ":
                token = item["symboltoken"]
                get_token.cache[symbol] = token
                return token
    raise ValueError(f"Token not found for {symbol}")

# ----------------------------------------------------------------------
# Technical helpers
# ----------------------------------------------------------------------
def rsi(prices, period=14):
    if len(prices) <= period:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains  = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)

def ema(prices, span=50):
    if len(prices) < span:
        return sum(prices) / len(prices) if prices else 0.0
    k = 2 / (span + 1)
    ema_val = prices[0]
    for p in prices[1:]:
        ema_val = p * k + ema_val * (1 - k)
    return ema_val

# ----------------------------------------------------------------------
# Main analysis loop
# ----------------------------------------------------------------------
stock_configs = {}

print("\nBrain nalysis Initiated")
for sym in STOCKS:
    try:
        hist = fetch_historic(sym)
        if len(hist) < 55:
            continue

        closes = [float(c[4]) for c in hist]          # close price
        volumes = [int(c[5]) for c in hist]

        current   = closes[-1]
        prev      = closes[-2]
        three_ago = closes[-3] if len(closes) >= 3 else prev

        mom_3d   = ((current - three_ago) / three_ago) * 100 if three_ago else 0
        day_chg  = ((current - prev) / prev) * 100 if prev else 0
        rsi_val  = rsi(closes)
        ema_50   = ema(closes)
        trend    = "BULL" if current > ema_50 else "BEAR"

        allow_short = sym not in RESTRICTED_LIST

        # Default neutral (Class B)
        cfg = {
            "class": "B",
            "allow_short": allow_short,
            "breakout_long": 0.005,
            "breakout_short": 0.005,
            "target": 0.010,
            "sl": 0.005,
            "leverage": 1.0
        }

        # ----- SNIPER (Oversold bounce) → Class A -----
        if rsi_val < 30 and day_chg > 0.1:
            cfg.update({
                "class": "A",
                "breakout_long": 0.001,
                "target": 0.020,
                "sl": 0.010,
                "leverage": 2.0
            })

        # ----- CLASS A (Bull momentum) -----
        elif mom_3d > 1.5 and rsi_val < 70 and day_chg > -0.5 and trend == "BULL":
            cfg.update({
                "class": "A",
                "breakout_long": 0.002,
                "target": 0.015,
                "leverage": 2.0
            })

        # ----- CLASS C (Bear / Rubber-Band) -----
        elif (mom_3d < -1.5 or day_chg < -1.5) and rsi_val > 30 and trend == "BEAR" and allow_short:
            cfg.update({
                "class": "C",
                "breakout_short": 0.002,
                "target": 0.015,
                "leverage": 2.0
            })

        elif rsi_val > 75 and allow_short:
            cfg.update({
                "class": "C",
                "breakout_short": 0.001,
                "target": 0.010,
                "sl": 0.005
            })

        stock_configs[sym] = cfg
        time.sleep(1)  

    except Exception as e:
        print(f"{sym} error: {e}")
        continue

# ----------------------------------------------------------------------
# Save config for Go bot
# ----------------------------------------------------------------------
with open("config.json", "w") as f:
    json.dump(stock_configs, f, indent=4)

print(f"\nBrain finished – {len(stock_configs)} strategies written to config.json")