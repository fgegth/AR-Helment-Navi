#!/bin/sh
# 快速重启 (不清除代码, 只杀进程+清缓存+重启)
killall -9 python3 2>/dev/null || true
sleep 2
find /opt/hud_navi -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
find /opt/hud_navi -name '*.pyc' -delete 2>/dev/null
cd /opt/hud_navi
export PYTHONPATH=/opt/hud_navi
export PYTHONUNBUFFERED=1
nohup /opt/miniforge3/bin/python3 main.py > /tmp/hud.log 2>&1 &
echo "restarted PID=$!"
