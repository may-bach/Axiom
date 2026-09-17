#!/bin/bash
cd /home/opc/Axiom

echo "=========================================="
echo "          AXIOM BOT STATUS                "
echo "=========================================="
if pgrep -x "axiom" > /dev/null; then
    PID=$(pgrep -x axiom)
    echo "Status: RUNNING (PID: $PID)"
    ps -p $PID -o pid,%cpu,%mem,etime,cmd
else
    echo "Status: STOPPED"
fi

if [ -f data/fresh_request_code.txt ]; then
    echo "Last Token: $(cat data/fresh_request_code.txt)"
    echo "Token Time: $(date -r data/fresh_request_code.txt '+%Y-%m-%d %H:%M:%S %Z')"
fi

echo ""
echo "=== RECENT AUTO-SYNC LOGS ==="
if [ -f logs/auto_sync.log ]; then
    tail -n 8 logs/auto_sync.log
else
    echo "No auto_sync logs yet."
fi

echo ""
echo "=== RECENT TRADE LOGS ==="
if [ -f logs/trades.log ]; then
    tail -n 12 logs/trades.log
else
    echo "No trade logs yet."
fi
echo "=========================================="
