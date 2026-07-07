#!/bin/sh
cd /opt/hud_navi
export PYTHONPATH=/opt/hud_navi
export PYTHONUNBUFFERED=1
exec /opt/miniforge3/bin/python3 main.py
