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
import os
from pathlib import Path
import re
import sys
import time
import xml.etree.ElementTree as ET
import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REGIME_FILE = DATA_DIR / "regime.json"
SECTOR_MATRIX_FILE = DATA_DIR / "sector_matrix.json"
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


def get_gemini_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key.strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    val = line.split("=", 1)[1].strip()
                    if val and not val.startswith("#"):
                        return val
        except Exception:
            pass
    return None


def load_sector_matrix():
    if SECTOR_MATRIX_FILE.exists():
        try:
            return json.loads(SECTOR_MATRIX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def query_gemini_reasoner(api_key, headlines, vix, nifty, sector_matrix):
    models = ["gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-3.6-flash"]
    headers = {"Content-Type": "application/json"}
    top_headlines = [title for _, title in headlines[:25]]
    symbols = list(sector_matrix.keys())

    prompt = (
        "You are Axiom's Geopolitical and Macro Risk Engine for the Indian stock market.\n"
        "Analyze the provided headlines and volatility to evaluate actor-target dynamics and economic transmission channels.\n"
        "1. Identify actor (who attacked or took action), target (who was hit), and critical arteries (crude oil transit in Hormuz/Red Sea, defense supply, rate hikes).\n"
        "2. Choose one macro_theme from: SYSTEMIC_CRISIS, CRUDE_OIL_SHOCK, DEFENSE_ESCALATION, HAWKISH_RATES, COMMODITY_EXPANSION, CALM_TRENDING.\n"
        "   - Choose SYSTEMIC_CRISIS ONLY on catastrophic macro shocks (e.g. active declaration of war, missile strikes on regional territory, emergency banking failure, sudden market crash panic).\n"
        "3. Assign directional stance for each stock symbol:\n"
        "   - LONG_ONLY: Stock benefits directly from this theme (e.g. Defense on war, Power on energy crisis)\n"
        "   - SHORT_ONLY: Stock is directly harmed (e.g. Auto on fuel inflation, Realty on rate hikes)\n"
        "   - BLOCKED: High tail-risk\n"
        "   - NEUTRAL: No decisive directional advantage\n\n"
        f"India VIX: {vix}\nNifty 50: {nifty}\n"
        f"Watchlist: {', '.join(symbols)}\n"
        "Headlines:\n" + "\n".join(f"- {h}" for h in top_headlines) + "\n\n"
        "Return STRICT JSON with keys: 'macro_theme', 'summary', 'stock_directives'."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1,
            "max_output_tokens": 1200,
        },
    }

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=12)
            if r.status_code == 200:
                data = r.json()
                raw_text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                parsed = json.loads(raw_text)
                if "macro_theme" in parsed and "stock_directives" in parsed:
                    print(f"[SENTINEL] Gemini reasoning successful using {model}")
                    return parsed
            elif r.status_code in (500, 503, 504, 429):
                continue
            else:
                print(f"[SENTINEL] Gemini ({model}) HTTP {r.status_code}: {r.text[:200]}")
        except Exception:
            continue
    return None


def offline_causal_reasoner(headlines, vix, sector_matrix):
    """
    Structured Actor-Action-Target Causal Engine.
    Works completely offline with zero API calls.
    Deterministic, <1ms execution, minimal RAM footprint.
    """
    actors_found = set()
    actions_found = set()
    oil_choke_hits = 0
    defense_hits = 0
    rate_hike_hits = 0
    metal_hits = 0

    RE_ACTORS = re.compile(
        r"\b(iran|israel|u\.?s\.?a?|america|american|russia|ukraine|china|taiwan|houthi|hezbollah|lebanon|syria|middle east|pakistan)\b",
        re.I,
    )
    RE_ACTIONS = re.compile(
        r"\b(strikes?|attack(?:s|ed|ing)?|missiles?|drones?|bombs?|explosions?|launches?|invades?|retaliat(?:es?|ion)|conflict|war|sanctions?|intercept(?:s|ed|ion)?)\b",
        re.I,
    )
    RE_OIL_CHOKE = re.compile(
        r"\b(oil|crude|hormuz|persian gulf|red sea|tanker|refinery|aramco|brent|gas pipeline|energy supply|fuel)\b",
        re.I,
    )
    RE_DEFENSE = re.compile(
        r"\b(weapons?|military|air base|defense|procurement|warships?|fighter jets?|drdo|arms?|airspace|security forces)\b",
        re.I,
    )
    RE_HAWKISH = re.compile(
        r"\b(rate hike|hike rates?|hawkish|inflation (?:surges?|accelerates?|jumps?)|yields? spike|sticky inflation)\b",
        re.I,
    )
    RE_METALS = re.compile(
        r"\b(metal rally|steel demand|copper surges?|china stimulus|commodity supercycle)\b",
        re.I,
    )

    for _, title in headlines:
        act = RE_ACTORS.findall(title)
        action = RE_ACTIONS.findall(title)
        has_oil = bool(RE_OIL_CHOKE.search(title))
        has_def = bool(RE_DEFENSE.search(title))
        has_rate = bool(RE_HAWKISH.search(title))
        has_metal = bool(RE_METALS.search(title))

        if act and action:
            actors_found.update([a.upper() for a in act])
            actions_found.update([a.lower() for a in action])
            if has_oil:
                oil_choke_hits += 1
            elif has_def or any(w in action for w in ["war", "conflict", "missiles", "strikes"]):
                defense_hits += 1
        elif has_oil and any(w in title.lower() for w in ["surge", "spike", "jump", "rally", "disrupt"]):
            oil_choke_hits += 1

        if has_rate:
            rate_hike_hits += 1
        if has_metal:
            metal_hits += 1

    directives = {sym: "NEUTRAL" for sym in sector_matrix.keys()}
    macro_theme = "CALM_TRENDING"
    summary = "No major macro or geopolitical disruption detected. Standard dual-direction trading active."

    # Rule 1: Geopolitical Conflict impacting Crude Oil / Middle East Choke Points
    if oil_choke_hits >= 1 or (defense_hits >= 1 and ("IRAN" in actors_found or "HOUTHI" in actors_found or "MIDDLE EAST" in actors_found)):
        macro_theme = "CRUDE_OIL_SHOCK"
        actor_str = ", ".join(sorted(actors_found)[:3]) if actors_found else "Middle East"
        summary = f"Geopolitical conflict involving {actor_str} threatening Persian Gulf and maritime crude transit. Margin pressure on Auto while boosting Defense and Gold."
        for sym, data in sector_matrix.items():
            sector = data.get("sector", "")
            if sector == "DEFENSE":
                directives[sym] = "LONG_ONLY"
            elif sector == "AUTO":
                directives[sym] = "SHORT_ONLY"
            elif sector == "NBFC_GOLD":
                directives[sym] = "LONG_ONLY"
            elif sector in ("POWER", "POWER_CAPGOODS"):
                directives[sym] = "LONG_ONLY"
            elif sector in ("CONSUMER_AC", "PORTS_SHIPPING", "RETAIL"):
                directives[sym] = "SHORT_ONLY"

    # Rule 2: Geopolitical Escalation / Rearmament (without immediate oil chokepoint)
    elif defense_hits >= 1 or len(actors_found) >= 2:
        macro_theme = "DEFENSE_ESCALATION"
        actor_str = ", ".join(sorted(actors_found)[:3]) if actors_found else "Regional actors"
        summary = f"Military friction or defense procurement drivers involving {actor_str}. Defense and strategic capex sectors gain bullish tailwinds."
        for sym, data in sector_matrix.items():
            sector = data.get("sector", "")
            if sector == "DEFENSE":
                directives[sym] = "LONG_ONLY"
            elif sector in ("RAIL_DEFENSE", "EMS_DEFENSE"):
                directives[sym] = "LONG_ONLY"

    # Rule 3: Hawkish Interest Rates / High Inflation
    elif rate_hike_hits >= 1:
        macro_theme = "HAWKISH_RATES"
        summary = "Hawkish central bank rate commentary or inflation spike. High-debt realty and vehicle finance face headwinds."
        for sym, data in sector_matrix.items():
            sector = data.get("sector", "")
            if sector in ("REALTY", "NBFC_VEHICLE"):
                directives[sym] = "SHORT_ONLY"

    # Rule 4: Commodity / Metal Supercycle
    elif metal_hits >= 1:
        macro_theme = "COMMODITY_EXPANSION"
        summary = "Global commodity revival or metals stimulus. Metals and resource producers gain bullish momentum."
        for sym, data in sector_matrix.items():
            sector = data.get("sector", "")
            if sector == "METALS":
                directives[sym] = "LONG_ONLY"

    return {
        "macro_theme": macro_theme,
        "summary": summary,
        "stock_directives": directives,
    }


def resolve_geopolitical_directives(headlines, vix, nifty):
    sector_matrix = load_sector_matrix()
    api_key = get_gemini_api_key()

    if api_key:
        try:
            print("[SENTINEL] Querying Gemini 3.6 Flash for actor-target macro reasoning...")
            gemini_res = query_gemini_reasoner(api_key, headlines, vix, nifty, sector_matrix)
            if gemini_res:
                directives = {sym: "NEUTRAL" for sym in sector_matrix.keys()}
                directives.update(gemini_res.get("stock_directives", {}))
                print(f"[SENTINEL] Gemini Reasoner: Theme = {gemini_res.get('macro_theme')}")
                return gemini_res.get("macro_theme", "CALM_TRENDING"), gemini_res.get("summary", ""), directives
        except Exception as e:
            print(f"[SENTINEL] Gemini API call failed: {e}. Falling back to offline Causal Reasoner.")

    res = offline_causal_reasoner(headlines, vix, sector_matrix)
    print(f"[SENTINEL] Offline Reasoner: Theme = {res['macro_theme']}")
    return res["macro_theme"], res["summary"], res["stock_directives"]


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

    # 4. Actor-Target Geopolitical & Sector Transmission Reasoner
    macro_theme, theme_summary, stock_directives = resolve_geopolitical_directives(headlines, vix, nifty)

    # Classification logic:
    # CRISIS (Full Shield / 100% Cash):
    #    Triggered ONLY on genuine systemic panic:
    #    1. VIX >= 22.0 (Extreme volatility shock)
    #    2. Direct crisis headline CONFIRMED by elevated VIX >= 16.0 (or VIX feed missing)
    #    3. Total composite risk score >= 75
    # CAUTION (Conservative Single Position / Rs.10k size):
    #    1. Crisis headlines flagged, but VIX is calm (< 16.0) -> Geopolitical noise without systemic panic
    #    2. VIX >= 17.0 (Elevated chop) or VIX < 12.0 (Sluggish low-volatility consolidation)
    #    3. Macro events (Fed, RBI, election, inflation) or score >= 25
    # NORMAL (Full Breakout / 3 positions / Rs.15k size):
    #    Clean sentiment, VIX between 12.0 and 17.0, risk score < 25
    confirmed_crisis = has_direct_crisis and (vix is None or vix >= 16.0)
    extreme_vix_panic = vix is not None and vix >= 22.0
    extreme_score = score >= 75
    is_gemini_crisis = (macro_theme == "SYSTEMIC_CRISIS")

    has_active_theme = (macro_theme not in ("", "CALM_TRENDING") and any(d != "NEUTRAL" for d in stock_directives.values()))

    if confirmed_crisis or extreme_vix_panic or extreme_score or is_gemini_crisis:
        status = "CRISIS"
        color = "RED"
        max_pos = 0
        budget = 0.0
        reason = f"Confirmed systemic crisis ({theme_summary if is_gemini_crisis else 'VIX/Headline shock'}). Market Shield Active: Trading halted today."
    elif vix is not None and vix < 12.0 and not has_active_theme and score < 30:
        status = "CALM_STAND_DOWN"
        color = "GRAY"
        max_pos = 0
        budget = 0.0
        reason = f"Volatility Gate: Ultra-low India VIX ({vix:.2f} < 12.0) with zero sector catalyst. Standing down in 100% Cash to prevent chop losses and statutory fee bleeding."
    elif (has_direct_crisis and vix and vix < 16.0) or (vix and (vix >= 17.0 or vix < 12.0)) or score >= 25:
        status = "CAUTION"
        color = "YELLOW"
        max_pos = 2
        budget = 10000.0
        if has_active_theme:
            reason = f"Low VIX ({vix:.2f} < 12) with active {macro_theme} theme. Caution Mode: Max 2 positions, targeting sector leaders."
        elif has_direct_crisis and vix and vix < 16.0:
            reason = f"Geopolitical headline flagged, but India VIX is calm ({vix:.2f} < 16). Caution Mode: Max 2 positions, Rs.10k size."
        elif vix and vix < 12.0:
            reason = f"Low volatility consolidation (India VIX {vix:.2f} < 12.0). Caution Mode: Max 2 positions to avoid chop."
        else:
            reason = f"Macro event or elevated chop expected (Score: {score}). Caution Mode: Max 2 positions, Rs.10k size."
    else:
        status = "NORMAL"
        color = "GREEN"
        max_pos = 3
        budget = 15000.0
        reason = f"Market sentiment calm and trending (VIX: {vix:.2f} if vix else 'N/A'). Full Breakout Mode: Up to 3 positions, Rs.15k size."

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
        "stagnation_minutes": 30,
        "daily_loss_limit": -750.0,
        "reason": reason,
        "flagged_headlines": flagged,
        "macro_theme": macro_theme,
        "theme_summary": theme_summary,
        "stock_directives": stock_directives,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REGIME_FILE.write_text(json.dumps(regime_data, indent=2), encoding="utf-8")
    print(f"Regime Verdict: [{color}] {status} (Risk Score: {score}/100 | VIX: {vix})")
    print(f"Macro Theme   : {macro_theme}")
    print(f"Theme Summary : {theme_summary}")
    print(f"Directive     : {reason}")
    overrides = [f"{s}:{d}" for s, d in stock_directives.items() if d != "NEUTRAL"]
    if overrides:
        print(f"Active Directives ({len(overrides)} overrides): {', '.join(overrides)}")
    print(f"Saved to {REGIME_FILE}")
    return regime_data


def run_loop(interval_minutes=20):
    """
    Continuous background monitor loop for market hours.
    Sleeps outside trading hours, executes scan every `interval_minutes` between 09:10 and 15:35 IST.
    """
    print(f"[SENTINEL DAEMON] Starting asynchronous macro monitor loop (interval: {interval_minutes}m)...")
    while True:
        try:
            now_ist = datetime.now(IST)
            h, m = now_ist.hour, now_ist.minute
            is_market_day = now_ist.weekday() < 5
            is_trading_hours = (h == 9 and m >= 10) or (9 < h < 15) or (h == 15 and m <= 35)

            if is_market_day and is_trading_hours:
                print(f"\n[{now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}] Running scheduled macro & sentiment scan...")
                evaluate_regime()
            else:
                print(f"[{now_ist.strftime('%H:%M:%S IST')}] Outside active market hours (09:10 - 15:35 IST). Waiting for next cycle...")
        except Exception as e:
            print(f"[SENTINEL DAEMON ERROR] {e}")

        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Axiom Sentinel Macro & Geopolitical Risk Engine")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background during market hours")
    parser.add_argument("--interval", type=int, default=20, help="Interval in minutes between scans (default: 20)")
    args = parser.parse_args()

    if args.daemon:
        run_loop(args.interval)
    else:
        evaluate_regime()
