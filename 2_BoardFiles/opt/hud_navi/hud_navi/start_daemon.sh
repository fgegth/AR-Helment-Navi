#!/bin/sh
mount -o remount,rw / 2>/dev/null
amixer -c 0 cset numid=2 1 2>/dev/null
amixer -c 0 cset numid=1 4 2>/dev/null
export PYTHONPATH=/opt/hud_navi
export PYTHONUNBUFFERED=1
start-stop-daemon -S -b -m -p /var/run/hud.pid -x /opt/miniforge3/bin/python3 -- /opt/hud_navi/main.py
