#!/bin/bash
cd /home/opc/Axiom

if pgrep -x "axiom" > /dev/null; then
    echo "Axiom is already running! PID: $(pgrep -x axiom)"
else
    echo "Starting Axiom in background..."
    nohup ./axiom >> logs/trades.log 2>&1 &
    sleep 1
    if pgrep -x "axiom" > /dev/null; then
        echo "Axiom launched successfully! PID: $(pgrep -x axiom)"
    else
        echo "Failed to start. Last log lines:"
        tail -n 10 logs/trades.log
    fi
fi

if pgrep -f "sentinel.py --daemon" > /dev/null; then
    echo "Sentinel Daemon is already running! PID: $(pgrep -f 'sentinel.py --daemon')"
else
    echo "Starting Sentinel Daemon (20m async monitor) in background..."
    nohup /usr/bin/python3 sentinel.py --daemon --interval 20 >> logs/sentinel.log 2>&1 &
    sleep 1
    if pgrep -f "sentinel.py --daemon" > /dev/null; then
        echo "Sentinel Daemon launched successfully! PID: $(pgrep -f 'sentinel.py --daemon')"
    fi
fi

