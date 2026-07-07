#!/bin/sh
# HUD V3.2 Guard — 导航主进程守护
cd /opt/hud_navi

# 启动前清理残留
killall -9 weston python3 v4l2-ctl drm_show 2>/dev/null
sleep 2

# 摄像头AI独立进程(自保活, 崩了自动重启)
/opt/miniforge3/bin/python3 /opt/hud_navi/camera_pulse.py >> /tmp/hud.log 2>&1 &

while true; do
    echo "=== Guard: $(date) ===" >> /tmp/hud.log
    PYTHONUNBUFFERED=1 /opt/miniforge3/bin/python3 main.py >> /tmp/hud.log 2>&1
    echo "=== exited, restart in 5s ===" >> /tmp/hud.log
    killall -9 python3 v4l2-ctl drm_show 2>/dev/null
    # 重启摄像头(如被killall杀掉)
    /opt/miniforge3/bin/python3 /opt/hud_navi/camera_pulse.py >> /tmp/hud.log 2>&1 &
    sleep 3
done
