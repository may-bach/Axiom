#!/usr/bin/env python3
"""
Test Gemini 3.6 / 3.1 Flash Actor-Target Macro Reasoning against real scenarios:
1. Geopolitical Conflict / War Shock (Sep 15 scenario)
2. Crude Oil & Inflation Shock
3. Normal Summer Consolidation (Calm market)
"""

import json
from pathlib import Path
from sentinel import query_gemini_reasoner, get_gemini_api_key

api_key = get_gemini_api_key()
if not api_key:
    print("Error: No GEMINI_API_KEY found in environment or .env")
    exit(1)

# Sample sector matrix of key symbols
sector_matrix = {
    "HAL": {"sector": "Defense", "beta_class": "A"},
    "BEL": {"sector": "Defense", "beta_class": "A"},
    "ONGC": {"sector": "Oil & Gas", "beta_class": "A"},
    "TVSMOTOR": {"sector": "Automobile", "beta_class": "A"},
    "MARUTI": {"sector": "Automobile", "beta_class": "B"},
    "DLF": {"sector": "Real Estate", "beta_class": "A"},
    "TATASTEEL": {"sector": "Metals", "beta_class": "A"},
    "HDFCBANK": {"sector": "Banking", "beta_class": "B"},
    "INFY": {"sector": "IT", "beta_class": "B"},
}

SCENARIOS = [
    {
        "name": "SCENARIO 1: Geopolitical War Shock (Like Sep 15)",
        "vix": 14.5,
        "nifty": 23150.0,
        "headlines": [
            ("Reuters", "Missile strikes reported across Red Sea maritime corridor, military escalation threatens Gulf crude transit"),
            ("Bloomberg", "Brent crude surges 6.8% above $92 as direct military conflict breaks out in Middle East"),
            ("Economic Times", "Global market panic: Dow futures tumble 500 points, Asian equities sell off aggressively"),
            ("Livemint", "Nifty set for 350-point gap down as geopolitical shockwaves rattle investor sentiment"),
            ("Moneycontrol", "Defense stocks rally in global trade while aviation, paint, and auto sectors face severe margin squeeze"),
        ]
    },
    {
        "name": "SCENARIO 2: Central Bank Rate Hike & Rupee Shock",
        "vix": 12.8,
        "nifty": 23400.0,
        "headlines": [
            ("Reuters", "Federal Reserve signals aggressive rate hike path as US core inflation tops 4.2%"),
            ("Economic Times", "Rupee breaches record low against US dollar, RBI prepares market intervention"),
            ("Livemint", "High-debt real estate developers and non-bank lenders face borrowing cost surge"),
            ("Moneycontrol", "IT exporters gain ground on currency depreciation while bank margins come under scrutiny"),
        ]
    },
    {
        "name": "SCENARIO 3: Calm Market Consolidation (Like Today)",
        "vix": 10.35,
        "nifty": 23550.0,
        "headlines": [
            ("Livemint", "Sensex trades in narrow 150-point range amidst lack of fresh global triggers"),
            ("Economic Times", "India VIX hovers near multi-year lows at 10.3 as institutional traders sell straddles"),
            ("Moneycontrol", "Corporate earnings season concludes with steady mid-single-digit profit growth"),
        ]
    }
]

print("=" * 80)
print(" LIVE TEST: GEMINI ACTOR-TARGET MACRO REASONING ENGINE")
print("=" * 80)

for s in SCENARIOS:
    print(f"\n>>> Running {s['name']} (India VIX: {s['vix']})")
    print("Feeding headlines to Gemini...")
    res = query_gemini_reasoner(api_key, s["headlines"], s["vix"], s["nifty"], sector_matrix)
    if res:
        print(f" Macro Theme Detected : {res.get('macro_theme')}")
        print(f" AI Transmission Reasoning : {res.get('summary')}")
        directives = res.get("stock_directives", {})
        print(" AI Directional Directives:")
        for sym, d in directives.items():
            if d != "NEUTRAL":
                print(f"   • {sym:<12} -> {d}")
    else:
        print("Gemini API call failed or timed out.")

print("\n" + "=" * 80)
