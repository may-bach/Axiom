#!/usr/bin/env python3
"""
Axiom Market Sentinel (Hybrid Macro & Volatility Regime Detector)
Combines:
  1. Financial News Sentiment (ET, Livemint, Moneycontrol RSS)
  2. Institutional Volatility (India VIX & Nifty 50)
Memory footprint: <35 MB RAM. Execution time: ~1.2s.
Classifies market regime into 🟢 NORMAL, 🟡 CAUTION, or 🔴 CRISIS SHIELD.
"""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path("/home/opc/Axiom") if Path("/home/opc/Axiom").exists() else Path(".")
DATA_DIR = ROOT / "data"
REGIME_FILE = DATA_DIR / "regime.json"
IST = timezone(timedelta(hours=5, minutes=30))

# Macro & Market Risk Keyword Lexicons
CRISIS_KEYWORDS = {
    r"\bwar\b": 45,
    r"\bmissile\b": 40,
    r"\bmilitary strike\b": 45,
    r"\binvasion\b": 45,
    r"\bsanctions\b": 30,
    r"\bflash crash\b": 50,
    r"\bmarket crash\b": 50,
    r"\bblack swan\b": 50,
    r"\bemergency meeting\b": 35,
    r"\bterror(?:ist|ism)?\b": 40,
    r"\bcircuit breaker\b": 40,
    r"\bgeopolitical tensions?\b": 25,
}

EVENT_KEYWORDS = {
    r"\brbi (?:mpc|policy|rate)\b": 18,
    r"\brepo rate\b": 15,
    r"\bfed(?:eral reserve)? rate\b": 15,
    r"\bfomc\b": 15,
    r"\bunion budget\b": 25,
    r"\belection (?:results?|counting|exit polls?)\b": 25,
    r"\binflation (?:spike|surges?|accelerates?)\b": 15,
    r"\bcrude oil (?:surges?|spikes?|jumps?)\b": 15,
}

CHOP_KEYWORDS = {
    r"\bchoppy\b": 12,
    r"\brange-?bound\b": 12,
    r"\bwhipsaw\b": 15,
    r"\bvolatile\b": 10,
    r"\bprofit booking\b": 8,
    r"\buncertainty\b": 10,
    r"\bconsolidation\b": 8,
}

BULLISH_TREND_KEYWORDS = {
    r"\brecord high\b": -10,
    r"\ball-?time high\b": -10,
    r"\bstrong rally\b": -12,
    r"\bbullish breakout\b": -15,
    r"\bglobal rally\b": -10,
    r"\brobust growth\b": -8,
}

FEEDS = [
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Livemint", "https://www.livemint.com/rss/markets"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/MCtopnews.xml"),
]


def fetch_market_volatility():
    """
    Fetches real-time India VIX (^INDIAVIX) and Nifty 50 (^NSEI)
    from institutional feeds. Fast HTTP GET (~0.15s), <1 MB RAM.
    """
    vix_data = {"vix": None, "vix_change_pct": 0.0, "nifty": None}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }

    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EINDIAVIX?interval=1d",
            headers=headers,
            timeout=4,
        )
        if r.status_code == 200:
            meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            ltp = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose")
            if ltp:
                chg = ((ltp - prev) / prev * 100) if prev else 0.0
                vix_data["vix"] = round(float(ltp), 2)
                vix_data["vix_change_pct"] = round(float(chg), 2)
    except Exception:
        pass

    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1d",
            headers=headers,
            timeout=4,
        )
        if r.status_code == 200:
            meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            nifty_ltp = meta.get("regularMarketPrice")
            if nifty_ltp:
                vix_data["nifty"] = round(float(nifty_ltp), 2)
    except Exception:
        pass

    return vix_data


def fetch_headlines():
    headlines = []
    seen_titles = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }

    for source_name, url in FEEDS:
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                for item in root.findall(".//item")[:20]:
                    title_elem = item.find("title")
                    if title_elem is not None and title_elem.text:
                        raw_title = title_elem.text.strip()
                        norm = re.sub(r"[^a-zA-Z0-9 ]", "", raw_title.lower())
                        norm_words = tuple(sorted(set(norm.split()[:8])))
                        if norm_words not in seen_titles:
                            seen_titles.add(norm_words)
                            headlines.append((source_name, raw_title))
        except Exception:
            pass

    return headlines


def analyze_sentiment(headlines, vix_val):
    crisis_score = 0
    event_score = 0
    chop_score = 0
    bull_score = 0
    flagged = []
    has_direct_crisis = False

    for source, title in headlines:
        title_lower = title.lower()

        # Check Crisis keywords
        for pattern, weight in CRISIS_KEYWORDS.items():
            if re.search(pattern, title_lower):
                crisis_score += weight
                has_direct_crisis = True
                flagged.append(f"[CRISIS] {title} ({source})")
                break

        # Check Macro Event keywords
        for pattern, weight in EVENT_KEYWORDS.items():
            if re.search(pattern, title_lower):
                event_score += weight
                flagged.append(f"[EVENT] {title} ({source})")
                break

        # Check Chop keywords
        for pattern, weight in CHOP_KEYWORDS.items():
            if re.search(pattern, title_lower):
                chop_score += weight
                flagged.append(f"[CHOP] {title} ({source})")
                break

        # Check Bullish Trend keywords
        for pattern, weight in BULLISH_TREND_KEYWORDS.items():
            if re.search(pattern, title_lower):
                bull_score += weight
                break

    # Cap categories
    event_score = min(event_score, 40)
    chop_score = min(chop_score, 30)

    # Volatility impact from India VIX (Mathematical option market sentiment)
    vix_modifier = 0
    vix_note = ""
    if vix_val is not None:
        if vix_val >= 22.0:
            vix_modifier = 35
            vix_note = f"[VIX ALERT] India VIX extreme fear ({vix_val:.2f} >= 22.0)"
            flagged.insert(0, vix_note)
        elif vix_val >= 17.0:
            vix_modifier = 15
            vix_note = f"[VIX NOTICE] India VIX elevated chop ({vix_val:.2f} >= 17.0)"
            flagged.insert(0, vix_note)
        elif vix_val <= 13.5:
            vix_modifier = -8  # Calm, trending market gives higher breakout win-rate

    total_score = crisis_score + event_score + chop_score + bull_score + vix_modifier
    total_score = max(0, min(100, total_score))

    return total_score, has_direct_crisis, flagged[:8]


def evaluate_regime():
    now_ist = datetime.now(IST)
    date_str = now_ist.strftime("%Y-%m-%d")
    ts_str = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")

    print(f"[{ts_str}] Axiom Market Sentinel running...")
    
    # 1. Institutional Volatility (India VIX & Nifty)
    vol_data = fetch_market_volatility()
    vix = vol_data.get("vix")
    vix_chg = vol_data.get("vix_change_pct", 0.0)
    nifty = vol_data.get("nifty")
    if vix:
        print(f"India VIX: {vix:.2f} ({vix_chg:+.2f}%) | Nifty: {nifty}")
    else:
        print("India VIX: Unavailable from feed; using pure news sentiment.")

    # 2. Financial News Headlines
    headlines = fetch_headlines()
    print(f"Collected {len(headlines)} unique headlines from financial feeds.")

    # 3. Hybrid Sentiment & Volatility Evaluation
    score, has_direct_crisis, flagged = analyze_sentiment(headlines, vix)

    # Classification logic:
    # 🔴 CRISIS (Full Shield / 100% Cash):
    #    Triggered ONLY on genuine systemic panic:
    #    1. VIX >= 22.0 (Extreme volatility shock)
    #    2. Direct crisis headline CONFIRMED by elevated VIX >= 16.0 (or VIX feed missing)
    #    3. Total composite risk score >= 75
    # 🟡 CAUTION (Conservative Single Position / ₹10k size):
    #    1. Crisis headlines flagged, but VIX is calm (< 16.0) -> Geopolitical noise without panic
    #    2. VIX >= 17.0 (Elevated chop) or VIX < 12.0 (Sluggish low-volatility consolidation)
    #    3. Macro events (Fed, RBI, election, inflation) or score >= 25
    # 🟢 NORMAL (Full Breakout / 3 positions / ₹15k size):
    #    Clean sentiment, VIX between 12.0 and 17.0, risk score < 25
    confirmed_crisis = has_direct_crisis and (vix is None or vix >= 16.0)
    extreme_vix_panic = vix is not None and vix >= 22.0
    extreme_score = score >= 75

    if confirmed_crisis or extreme_vix_panic or extreme_score:
        status = "CRISIS"
        color = "RED"
        max_pos = 0
        budget = 0.0
        reason = f"Confirmed macro crisis or volatility shock (VIX: {vix if vix else 'N/A'}, Score: {score}). Market Shield Active: Trading disabled today."
    elif (has_direct_crisis and vix and vix < 16.0) or (vix and (vix >= 17.0 or vix < 12.0)) or score >= 25:
        status = "CAUTION"
        color = "YELLOW"
        max_pos = 1
        budget = 10000.0
        if has_direct_crisis and vix and vix < 16.0:
            reason = f"Geopolitical headline flagged, but India VIX is calm ({vix:.2f} < 16). Caution Mode: Max 1 position, Rs.10k size."
        elif vix and vix < 12.0:
            reason = f"Low volatility consolidation (India VIX {vix:.2f} < 12.0). Caution Mode: Max 1 position to avoid chop."
        else:
            reason = f"Macro event or elevated chop expected (Score: {score}). Caution Mode: Max 1 position, Rs.10k size."
    else:
        status = "NORMAL"
        color = "GREEN"
        max_pos = 3
        budget = 15000.0
        reason = "Market sentiment calm and trending. Full Breakout Mode: Up to 3 positions, Rs.15k size."

    regime_data = {
        "date": date_str,
        "timestamp": ts_str,
        "india_vix": vix,
        "vix_change_pct": vix_chg,
        "nifty_ltp": nifty,
        "risk_score": score,
        "status": status,
        "color": color,
        "max_positions": max_pos,
        "position_budget": budget,
        "stagnation_minutes": 45,
        "daily_loss_limit": -750.0,
        "reason": reason,
        "flagged_headlines": flagged,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REGIME_FILE.write_text(json.dumps(regime_data, indent=2), encoding="utf-8")
    print(f"Regime Verdict: [{color}] {status} (Risk Score: {score}/100 | VIX: {vix})")
    print(f"Directive: {reason}")
    print(f"Saved to {REGIME_FILE}")
    return regime_data


if __name__ == "__main__":
    evaluate_regime()
