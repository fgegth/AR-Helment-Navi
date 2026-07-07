#!/usr/bin/env python3
"""
HUD 守护进程 — 自我守护化(double-fork) + 崩溃自动重启
完全不依赖 Shell (nohup/setsid/start-stop-daemon), ADB退出也不影响
"""
import os, sys, time, subprocess

def daemonize():
    """标准Unix双fork守护化: 脱离终端, 成为init子进程"""
    if os.fork() > 0:
        sys.exit(0)  # 父进程退出
    os.setsid()       # 新会话
    if os.fork() > 0:
        sys.exit(0)  # 子进程退出, 孙进程被init(PID=1)收养
    # 孙进程继续: 已完全脱离ADB Shell

if __name__ == '__main__':
    daemonize()

    os.chdir('/opt/hud_navi')
    # 重定向标准IO到/dev/null
    sys.stdin = open('/dev/null', 'r')
    sys.stdout = open('/dev/null', 'w')
    sys.stderr = open('/dev/null', 'w')

    while True:
        with open('/tmp/hud.log', 'a') as f:
            f.write(f'=== Daemon: {time.ctime()} ===\n')
            f.flush()
            proc = subprocess.Popen(
                ['/opt/miniforge3/bin/python3', 'main.py'],
                stdout=f, stderr=subprocess.STDOUT,
                env={
                    **os.environ,
                    'PYTHONPATH': '/opt/hud_navi',
                    'PYTHONUNBUFFERED': '1',
                    'LD_LIBRARY_PATH': '/opt/hud_navi/piper:' + os.environ.get('LD_LIBRARY_PATH', ''),
                }
            )
            proc.wait()
            f.write(f'=== exited code={proc.returncode}, restart in 10s ===\n')
        time.sleep(10)
