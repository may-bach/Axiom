#!/bin/bash
cd /home/opc/Axiom

echo "=========================================="
echo "          AXIOM P&L & TRADE REPORT        "
echo "=========================================="
echo "Report Time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo ""

# Check for Morning Authentication Alert
if [ -f data/auth_alert.json ]; then
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "  CRITICAL ALERT: AUTHENTICATION FAILED!  "
    python3 -c "
import json
try:
    with open('data/auth_alert.json') as f:
        d = json.load(f)
    print('  Timestamp :', d.get('timestamp'))
    print('  Error     :', d.get('error'))
    print('  Action    :', d.get('action_required'))
except Exception:
    pass
"
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo ""
fi

# Show Market Sentinel Regime
if [ -f data/regime.json ]; then
    python3 -c "
import json
try:
    with open('data/regime.json') as f:
        r = json.load(f)
    col = r.get('color', '')
    status = r.get('status', 'UNKNOWN')
    score = r.get('risk_score', 0)
    max_pos = r.get('max_positions', 0)
    sizing = r.get('position_budget', 0.0)
    directive = r.get('reason', '')
    vix = r.get('india_vix')
    vix_chg = r.get('vix_change_pct', 0.0)
    nifty = r.get('nifty_ltp')
    print('=== MARKET SENTINEL REGIME ===')
    print(f'Status     : [{col}] {status} (Risk Score: {score}/100)')
    if vix:
        print(f'India VIX  : {vix:.2f} ({vix_chg:+.2f}%) | Nifty: {nifty}')
    print(f'Max Pos    : {max_pos} concurrent | Sizing: ₹{sizing:.0f}')
    print(f'Directive  : {directive}')
    if r.get('flagged_headlines'):
        print('Headlines  :')
        for h in r['flagged_headlines'][:2]:
            print(f'  • {h}')
    print('')
except Exception:
    pass
"
fi

if [ ! -f logs/trades.log ]; then
    echo "No trades log file found yet."
    exit 0
fi

# Show all today's Exits and calculate total P&L
TODAY=$(date '+%Y-%m-%d')
echo "=== TODAY'S COMPLETED TRADES ($TODAY) ==="
grep "\[$TODAY" logs/trades.log | grep -E "EXIT (LONG|SHORT)" | sort -u > /tmp/today_exits.txt 2>/dev/null

if [ -s /tmp/today_exits.txt ]; then
    cat /tmp/today_exits.txt
    echo ""
    python3 -c "
import re
pnl_total = 0.0
wins = 0
losses = 0
with open('/tmp/today_exits.txt') as f:
    for line in f:
        m = re.search(r'P&L:\s*₹?([-\d.]+)', line)
        if m:
            val = float(m.group(1))
            pnl_total += val
            if val > 0: wins += 1
            elif val < 0: losses += 1
print('------------------------------------------')
print(f'Total Closed Trades Today : {wins + losses}')
print(f'Win / Loss                : {wins}W / {losses}L')
print(f'Net Paper P&L Today       : ₹{pnl_total:+.2f}')
print('------------------------------------------')
"
else
    echo "No closed trades logged yet today ($TODAY)."
    echo "(Normal NSE market trading hours: 09:15 AM - 03:30 PM IST)."
fi

echo ""
echo "=== RECENT TRADE ENTRIES TODAY ($TODAY) ==="
grep "\[$TODAY" logs/trades.log | grep -E "ENTRY (LONG|SHORT)" | sort -u | tail -n 8 2>/dev/null || echo "None yet today."

# Show any past Daily Summaries
if grep -q "DAILY TRADE & P&L SUMMARY" logs/trades.log; then
    echo ""
    echo "=== LATEST RECORDED SUMMARY ==="
    grep -A 8 "DAILY TRADE & P&L SUMMARY" logs/trades.log | tail -n 9
fi

echo "=========================================="
