"""
AI 预警业务层 — V2.0
接收 AI 检测结果 → 防抖过滤 → 分级 → 写 nav_state → 触发语音

设计:
  - 防抖: 连续 N 帧同向变化才生效 (ALERT_DEBOUNCE_FRAMES)
  - 分级: NONE(0)/WATCH(1)/WARNING(2)/DANGER(3)
  - 语音去重: 同等级 10s 内不重复播
"""

import time
import logging

from camera_config import (
    ALERT_DEBOUNCE_FRAMES,
    AI_VOICE_COOLDOWN,
)

logger = logging.getLogger(__name__)

# 预警等级 (与 ai_detect 对齐)
ALERT_NONE = 0
ALERT_WATCH = 1
ALERT_WARNING = 2
ALERT_DANGER = 3

# 全局状态
_latest_alert = {"level": 0, "msg": "", "vehicles": 0}

# 防抖
_debounce_level = 0
_debounce_count = 0

# 语音去重
_last_voice_level = 0
_last_voice_time = 0.0


def handle_detection(result: dict):
    """
    AI 推理回调入口 (由 ai_monitor 调用)
    result: {level, msg, vehicles, speed_index, biggest, frame_seq, frame_ts}
    """
    global _latest_alert, _debounce_level, _debounce_count
    global _last_voice_level, _last_voice_time

    level = result.get("level", 0)
    msg = result.get("msg", "")
    vehicles = result.get("vehicles", 0)

    # ---- 防抖: 连续 N 帧同等级才生效 ----
    if level == _debounce_level:
        _debounce_count += 1
    else:
        _debounce_level = level
        _debounce_count = 1

    if _debounce_count < ALERT_DEBOUNCE_FRAMES:
        return  # 未达到防抖阈值, 不更新状态

    # ---- 更新全局状态 ----
    _latest_alert = {
        "level": level,
        "msg": msg,
        "vehicles": vehicles,
        "speed_index": result.get("speed_index", 0),
        "biggest": result.get("biggest", ""),
        "frame_seq": result.get("frame_seq", 0),
    }

    # ---- 写入 nav_state (HUD + HTTP 读取) ----
    try:
        from nav_state import state
        with state as s:
            s.camera_alert_level = level
            s.camera_alert_msg = msg
            s.camera_vehicles = vehicles
            if level >= ALERT_DANGER:
                s.error_message = "⚠ " + msg
            elif level >= ALERT_WARNING:
                if not s.error_message or "⚠" not in (s.error_message or ""):
                    s.error_message = msg
    except Exception as e:
        logger.debug(f"nav_state 写入失败: {e}")

    # ---- 语音播报 (去重) ----
    if level >= ALERT_WARNING:
        now = time.time()
        if (level != _last_voice_level
                or now - _last_voice_time > AI_VOICE_COOLDOWN):
            _last_voice_level = level
            _last_voice_time = now
            # 异步播报 (speak_helper)
            try:
                import threading as _th
                _th.Thread(
                    target=lambda: _speak_alert(msg),
                    daemon=True
                ).start()
            except Exception:
                pass


def _speak_alert(msg: str):
    """异步播报警告"""
    try:
        from speak_helper import speak
        speak(msg)
    except Exception:
        pass


def get_alert_status() -> dict:
    """HTTP API: 获取当前预警状态"""
    return dict(_latest_alert)


def reset_alert():
    """停止摄像头时重置预警状态"""
    global _latest_alert, _debounce_level, _debounce_count
    _latest_alert = {"level": 0, "msg": "", "vehicles": 0}
    _debounce_level = 0
    _debounce_count = 0
    try:
        from nav_state import state
        with state as s:
            s.camera_alert_level = 0
            s.camera_alert_msg = ""
            s.camera_vehicles = 0
    except Exception:
        pass
