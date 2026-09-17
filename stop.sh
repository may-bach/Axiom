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
