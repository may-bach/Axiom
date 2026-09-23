#!/bin/bash
cd /home/opc/Axiom

if pgrep -x "axiom" > /dev/null; then
    PID=$(pgrep -x axiom)
    echo "Stopping Axiom (PID: $PID)..."
    kill -15 $PID
    sleep 1
    if pgrep -x "axiom" > /dev/null; then
        kill -9 $PID
    fi
    echo "Axiom stopped."
else
    echo "Axiom is not running."
fi

if pgrep -f "sentinel.py --daemon" > /dev/null; then
    SPID=$(pgrep -f "sentinel.py --daemon")
    echo "Stopping Sentinel Daemon (PID: $SPID)..."
    kill -15 $SPID
    sleep 1
    if pgrep -f "sentinel.py --daemon" > /dev/null; then
        kill -9 $SPID
    fi
    echo "Sentinel Daemon stopped."
fi

