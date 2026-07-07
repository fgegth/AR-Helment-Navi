#!/bin/sh
# HUD Watchdog V3 — uses system Weston (already started by init)
PYTHON=/opt/miniforge3/bin/python3
LOG=/tmp/hud.log
cd /opt/hud_navi

while true; do
    echo "=== Watchdog: $(date) ===" >> $LOG
    echo "  Starting main.py (Wayland backend)..." >> $LOG
    export XDG_RUNTIME_DIR=/var/run
    export SDL_VIDEODRIVER=wayland
    $PYTHON main.py >> $LOG 2>&1
    echo "=== main.py exited, restart in 10s ===" >> $LOG
    sleep 10
done
