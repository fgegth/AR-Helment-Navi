#!/bin/sh
# HUD 快速部署+重启 (ADB安全版)
# 原理: 清缓存+推代码 → reboot让init启动 → 避开ADB Shell进程被杀问题
set -e
echo "=== HUD Deploy ==="

# 1. 清缓存
echo "[1/3] 清理pyc..."
rm -rf /opt/hud_navi/__pycache__ /opt/hud_navi/hud_navi/__pycache__ 2>/dev/null || true
find /opt/hud_navi -name '*.pyc' -delete 2>/dev/null || true
echo "      done"

# 2. 强制杀死旧进程
echo "[2/3] 停止旧进程..."
killall -9 python3 2>/dev/null || true
killall -9 python 2>/dev/null || true
sleep 2
echo "      done"

# 3. 重启(板子init会自动启动S99hudnavi)
echo "[3/3] 重启板子(60秒后自动上线)..."
reboot
