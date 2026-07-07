#!/bin/sh
cd /opt/hud_navi
export PYTHONPATH=/opt/hud_navi
export PYTHONUNBUFFERED=1
amixer -c 0 cset numid=2 1 2>/dev/null
amixer -c 0 cset numid=1 4 2>/dev/null
trap '' HUP INT TERM
/opt/miniforge3/bin/python3 main.py > /tmp/hud.log 2>&1
