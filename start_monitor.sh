#!/bin/bash
# Start the Polymarket monitor in the background.
# Usage: ./start_monitor.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill any existing monitor
pkill -f "python3 polymarket_monitor.py" 2>/dev/null && echo "Killed existing monitor." && sleep 1

cd "$SCRIPT_DIR"
nohup python3 polymarket_monitor.py > nohup.out 2>&1 &
PID=$!

echo "Monitor started (PID $PID)"
echo "Logs: tail -f $SCRIPT_DIR/nohup.out"
echo "Heartbeat: tail $SCRIPT_DIR/data/heartbeat.txt"
