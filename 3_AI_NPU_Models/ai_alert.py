"""
AI 预警集成 — 连接 ai_detect 到 HUD + HTTP + 语音
"""
import threading
from ai_detect import run_detection_loop, ALERT_WARNING, ALERT_DANGER

_alert_thread = None
_latest_alert = {"level": 0, "msg": "", "vehicles": 0}

def start_ai_monitor():
    """启动 AI 检测线程 (main.py 调用)"""
    global _alert_thread
    if _alert_thread and _alert_thread.is_alive():
        return
    _alert_thread = threading.Thread(target=_run, daemon=True, name="AI-Detect")
    _alert_thread.start()

def _run():
    def on_alert(alert):
        global _latest_alert
        _latest_alert = alert
        # 写入导航状态
        try:
            from nav_state import state
            with state as s:
                if alert["level"] >= ALERT_DANGER:
                    s.error_message = "⚠ " + alert["msg"]
                elif alert["level"] >= ALERT_WARNING:
                    if not s.error_message or "⚠" not in s.error_message:
                        s.error_message = alert["msg"]
        except Exception:
            pass
    run_detection_loop(on_alert)

def get_alert_status() -> dict:
    """获取当前预警状态 (HTTP API 调用)"""
    return _latest_alert

def stop_ai_monitor():
    global _alert_thread
    _alert_thread = None
