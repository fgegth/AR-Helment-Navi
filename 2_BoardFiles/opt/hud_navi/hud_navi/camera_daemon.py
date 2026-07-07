"""
摄像头AI守护进程 — 独立进程, 和导航系统分离
崩溃不影响main.py, guard.sh自动重启
"""
import sys, os, time, logging
sys.path.insert(0, '/opt/hud_navi')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [CamD] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('CamD')

logger.info("摄像头AI守护进程启动")

# 等系统稳定
time.sleep(10)

# 开摄像头
try:
    from camera_manager import camera
    if not camera.open():
        logger.error("摄像头打开失败")
        sys.exit(1)
    logger.info("摄像头已打开")
except Exception as e:
    logger.error(f"摄像头异常: {e}")
    sys.exit(1)

# 启动AI
try:
    from ai_alert import handle_detection
    from ai_monitor import AIMonitor
    ai = AIMonitor()
    ai.start(callback=handle_detection)
    logger.info("AI推理已启动")
except Exception as e:
    logger.error(f"AI异常: {e}")
    camera.close()
    sys.exit(1)

# 持续运行, 写HUD预警状态
logger.info("守护进程运行中...")
try:
    while True:
        time.sleep(5)
        from camera_manager import camera as cam
        s = cam.get_status()
        if not s['running']:
            logger.warning("摄像头已停止, 退出")
            break
        from nav_state import state
        snap = state.get_snapshot()
        if snap.camera_alert_level > 0:
            logger.info(f"预警: L{snap.camera_alert_level} 车{snap.camera_vehicles} {snap.camera_alert_msg}")
except KeyboardInterrupt:
    pass
finally:
    camera.close()
    from ai_alert import reset_alert
    reset_alert()
    logger.info("守护进程退出")
