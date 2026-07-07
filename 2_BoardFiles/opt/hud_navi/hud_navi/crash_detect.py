"""
碰撞检测与紧急求救系统
基于 IMU 加速度计的骑行碰撞检测 + 阿里云短信 SOS

检测流程:
  IMU 100Hz采样 → 加速度幅值>6g & 加加速度>50g/s → 滑动窗口60%确认
  → 触发(状态1) → 10秒倒计时(状态2, 可取消) → 自动SOS(状态3)

降级: 无IMU → 仅记录位置不检测碰撞; 无短信 → 文件系统记录
"""
import os
import json
import time
import math
import logging
import threading
import hashlib
import hmac
import base64
import urllib.request
import urllib.parse
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Tuple

from config import (
    IMU_ACCEL_THRESHOLD, IMU_JERK_THRESHOLD, IMU_SLIDING_WINDOW,
    IMU_CRASH_CONFIRM_RATIO, IMU_SAMPLE_RATE,
    SOS_COUNTDOWN_SEC, SOS_PRE_CRASH_BUFFER_SEC,
    SOS_ALIYUN_ACCESS_KEY, SOS_ALIYUN_ACCESS_SECRET,
    SOS_ALIYUN_SIGN_NAME, SOS_ALIYUN_TEMPLATE_CODE,
    SOS_PHONE_NUMBERS, SOS_SEND_COOLDOWN_SEC,
    SOS_ACCIDENT_DATA_DIR, SOS_FALLBACK_LOG,
)
from nav_state import state

logger = logging.getLogger(__name__)

# IMU 可选导入
try:
    from sensor_fusion import IMUReader
    _imu_available = True
except ImportError:
    _imu_available = False
    IMUReader = None

# HTTP 请求库检查
try:
    import requests
    _requests_available = True
except ImportError:
    _requests_available = False


# ================================================================
# 碰撞检测器
# ================================================================

class CrashDetector:
    """
    骑行碰撞检测 + 紧急求救

    状态机:
      0=空闲 (监控中)
      1=已触发 (等待确认)
      2=倒计时中 (可取消)
      3=求救已发送 (冷却中)
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # IMU
        self._imu: Optional[IMUReader] = None
        self._imu_ok = False
        if _imu_available and IMUReader is not None:
            try:
                self._imu = IMUReader()
                self._imu_ok = True
                logger.info("碰撞检测 IMU 已初始化")
            except Exception as e:
                logger.warning(f"碰撞检测 IMU 初始化失败: {e}")

        # 滑动窗口加速度数据
        self._accel_buffer: deque = deque(maxlen=IMU_SLIDING_WINDOW)

        # 状态机
        self._state: int = 0           # 0-3
        self._countdown_start: float = 0.0
        self._trigger_time: float = 0.0
        self._trigger_location: Optional[Tuple[float, float]] = None

        # SOS 冷却
        self._last_sos_time: float = 0.0

        # 视频环形缓冲区 (预碰撞30秒)
        self._video_buffer: deque = deque(maxlen=SOS_PRE_CRASH_BUFFER_SEC * 2)

        # 事故数据
        os.makedirs(SOS_ACCIDENT_DATA_DIR, exist_ok=True)

        logger.info(f"碰撞检测器就绪 (IMU={'可用' if self._imu_ok else '不可用'}, "
                    f"SMS={'已配置' if SOS_ALIYUN_ACCESS_KEY else '未配置'})")

    # ================================================================
    # 公开接口
    # ================================================================

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="CrashDet"
        )
        self._thread.start()
        logger.info("碰撞检测线程已启动 (100Hz)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._imu:
            self._imu.close()
        logger.info("碰撞检测已停止")

    def cancel_sos(self) -> bool:
        """
        用户取消 SOS 倒计时 (从 HTTP/HUD 调用)

        返回: True=已取消, False=无活动倒计时
        """
        if self._state == 2:
            self._state = 0
            self._countdown_start = 0
            logger.info("用户取消了 SOS 倒计时")
            with state as s:
                s.crash_alert = 0
                s.sos_countdown = 0
            return True
        return False

    def get_sos_status(self) -> dict:
        """获取当前 SOS 状态"""
        return {
            "active": self._state >= 2,
            "state": self._state,
            "countdown": self._get_countdown(),
            "location": self._trigger_location,
            "trigger_time": self._trigger_time,
        }

    # ================================================================
    # 主监控循环 (100Hz)
    # ================================================================

    def _monitor_loop(self):
        """主循环: IMU 采样 + 碰撞分析 + 状态机"""
        interval = 1.0 / IMU_SAMPLE_RATE
        last_state_write = time.time()

        while self._running:
            t_start = time.time()

            # 1. 读取 IMU
            imu = None
            if self._imu_ok and self._imu:
                try:
                    imu = self._imu.read()
                except Exception:
                    pass

            if imu is None:
                # 无 IMU → 仅保持视频缓冲
                time.sleep(0.5)
                continue

            # 2. 存入滑动窗口
            self._accel_buffer.append({
                "t": time.time(),
                "ax": imu["ax"], "ay": imu["ay"], "az": imu["az"],
                "accel_mag": imu["accel_mag"],
            })

            # 3. 碰撞检测 (仅在空闲状态)
            if self._state == 0 and len(self._accel_buffer) >= IMU_SLIDING_WINDOW:
                if self._analyze_crash():
                    self._trigger_crash()

            # 4. 状态机更新
            if self._state == 2:
                # 倒计时中
                remaining = self._get_countdown()
                if remaining <= 0:
                    self._send_sos()
                else:
                    # 更新视频缓冲
                    self._update_video_buffer()

            elif self._state == 3:
                # SOS 已发送, 等待冷却
                if time.time() - self._last_sos_time > SOS_SEND_COOLDOWN_SEC:
                    self._state = 0

            # 5. 写入 NavState (1Hz 降频)
            now = time.time()
            if now - last_state_write >= 1.0:
                self._write_to_state()
                last_state_write = now

            # 6. 帧率控制
            elapsed = time.time() - t_start
            if elapsed < interval:
                time.sleep(interval - elapsed)

    # ================================================================
    # 碰撞检测算法
    # ================================================================

    def _analyze_crash(self) -> bool:
        """
        分析滑动窗口中的加速度数据, 判定是否发生碰撞

        判定条件 (AND):
          1. 加速度幅值 > IMU_ACCEL_THRESHOLD (6g)
          2. 加加速度 > IMU_JERK_THRESHOLD (50g/s)
          3. 滑动窗口中超过 IMU_CRASH_CONFIRM_RATIO (60%) 样本满足以上条件
        """
        if len(self._accel_buffer) < IMU_SLIDING_WINDOW:
            return False

        crash_samples = 0
        total_samples = 0

        # 检测每个样本
        buf_list = list(self._accel_buffer)
        for i in range(1, len(buf_list)):
            curr_mag = buf_list[i]["accel_mag"]
            prev_mag = buf_list[i - 1]["accel_mag"]
            dt = buf_list[i]["t"] - buf_list[i - 1]["t"]

            if dt <= 0:
                continue

            jerk = abs(curr_mag - prev_mag) / dt

            if curr_mag > IMU_ACCEL_THRESHOLD and jerk > IMU_JERK_THRESHOLD:
                crash_samples += 1
            total_samples += 1

        if total_samples == 0:
            return False

        ratio = crash_samples / total_samples
        logger.debug(f"碰撞检测: {crash_samples}/{total_samples} ({ratio:.1%}), "
                     f"阈值={IMU_CRASH_CONFIRM_RATIO:.1%}")

        return ratio >= IMU_CRASH_CONFIRM_RATIO

    def _trigger_crash(self):
        """触发碰撞检测"""
        self._state = 1
        self._trigger_time = time.time()
        self._countdown_start = time.time()

        # 获取当前位置
        snap = state.get_snapshot()
        if snap.current_position:
            self._trigger_location = snap.current_position
        elif snap.fused_position:
            self._trigger_location = snap.fused_position

        logger.warning(f"🚨 碰撞触发! 位置={self._trigger_location}, "
                       f"开始{SOS_COUNTDOWN_SEC}秒倒计时")

        # 立即进入倒计时状态
        self._state = 2

    def _get_countdown(self) -> int:
        """获取剩余倒计时秒数"""
        if self._state != 2 or self._countdown_start == 0:
            return 0
        elapsed = time.time() - self._countdown_start
        return max(0, SOS_COUNTDOWN_SEC - int(elapsed))

    # ================================================================
    # SOS 发送
    # ================================================================

    def _send_sos(self):
        """发送 SOS 求救信号"""
        self._state = 3
        self._last_sos_time = time.time()
        sos_time = datetime.now(timezone.utc).isoformat()

        logger.warning("🆘 发送 SOS 求救信号!")

        # 获取位置
        loc = self._trigger_location
        if loc is None:
            snap = state.get_snapshot()
            loc = snap.current_position or snap.fused_position

        success = False

        # 方法1: 阿里云短信 API
        if SOS_ALIYUN_ACCESS_KEY and SOS_PHONE_NUMBERS:
            success = self._send_aliyun_sms(loc, sos_time)

        # 方法2: 文件系统兜底
        if not success:
            self._save_sos_local(loc, sos_time)

        # 保存事故数据
        self._save_accident_data(loc, sos_time)

        # 更新 NavState
        with state as s:
            s.crash_alert = 3
            s.sos_countdown = 0
            s.sos_location = loc
            s.sos_time = sos_time

    def _send_aliyun_sms(self, location, sos_time: str) -> bool:
        """
        通过阿里云短信 API 发送 SOS

        API: dysmsapi.aliyuncs.com
        签名算法: HMAC-SHA1 (阿里云 V1 签名)
        """
        try:
            if not location:
                return False

            lat, lon = location
            loc_text = f"({lat:.5f}, {lon:.5f})"

            # 构建请求参数
            params = {
                "AccessKeyId": SOS_ALIYUN_ACCESS_KEY,
                "Action": "SendSms",
                "Format": "JSON",
                "PhoneNumbers": SOS_PHONE_NUMBERS,
                "SignName": SOS_ALIYUN_SIGN_NAME,
                "TemplateCode": SOS_ALIYUN_TEMPLATE_CODE,
                "TemplateParam": json.dumps({
                    "location": loc_text,
                    "time": sos_time[:19],
                }, ensure_ascii=False),
                "SignatureMethod": "HMAC-SHA1",
                "SignatureNonce": str(int(time.time() * 1000000)),
                "SignatureVersion": "1.0",
                "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "Version": "2017-05-25",
                "RegionId": "cn-hangzhou",
            }

            # 签名
            sorted_keys = sorted(params.keys())
            canonicalized = "&".join([
                f"{urllib.parse.quote_plus(k)}={urllib.parse.quote_plus(str(params[k]))}"
                for k in sorted_keys
            ])
            string_to_sign = f"POST&{urllib.parse.quote_plus('/')}&{urllib.parse.quote_plus(canonicalized)}"
            key = SOS_ALIYUN_ACCESS_SECRET + "&"
            signature = base64.b64encode(
                hmac.new(key.encode(), string_to_sign.encode(), hashlib.sha1).digest()
            ).decode()
            params["Signature"] = signature

            # 发送请求
            url = "https://dysmsapi.aliyuncs.com/"
            data = urllib.parse.urlencode(params).encode()

            if _requests_available:
                resp = requests.post(url, data=data, timeout=10)
                result = resp.json()
            else:
                req = urllib.request.Request(url, data=data, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())

            if result.get("Code") == "OK":
                logger.info(f"SOS 短信已发送: {SOS_PHONE_NUMBERS}")
                return True
            else:
                logger.error(f"SOS 短信发送失败: {result}")
                return False

        except Exception as e:
            logger.error(f"SOS 短信发送异常: {e}")
            return False

    def _save_sos_local(self, location, sos_time: str):
        """本地文件兜底记录 SOS"""
        try:
            os.makedirs(os.path.dirname(SOS_FALLBACK_LOG), exist_ok=True)
            record = {
                "time": sos_time,
                "location": location,
                "phones": SOS_PHONE_NUMBERS,
                "status": "failed_to_send_sms",
            }
            with open(SOS_FALLBACK_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(f"SOS 记录已保存到本地: {SOS_FALLBACK_LOG}")
        except Exception as e:
            logger.error(f"SOS 本地记录失败: {e}")

    def _save_accident_data(self, location, sos_time: str):
        """保存事故前后传感器数据"""
        try:
            fname = datetime.now().strftime("%Y%m%d_%H%M%S") + "_crash.json"
            path = os.path.join(SOS_ACCIDENT_DATA_DIR, fname)

            data = {
                "time": sos_time,
                "location": location,
                "crash_alert": 3,
                "accel_data": list(self._accel_buffer),
                "video_frame_count": len(self._video_buffer),
            }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"事故数据已保存: {path}")
        except Exception as e:
            logger.error(f"事故数据保存失败: {e}")

    # ================================================================
    # 视频缓冲
    # ================================================================

    def _update_video_buffer(self):
        """维护碰撞前30秒视频帧环形缓冲区"""
        try:
            from camera_capture import capture_frame
            path = "/tmp/crash_buffer_frame.jpg"
            if capture_frame():
                self._video_buffer.append({
                    "time": time.time(),
                    "path": path,
                })
        except Exception:
            pass

    # ================================================================
    # NavState 同步
    # ================================================================

    def _write_to_state(self):
        """写入 NavState (1Hz)"""
        try:
            countdown = self._get_countdown()
            with state as s:
                s.crash_alert = self._state
                s.sos_countdown = countdown
                if self._trigger_location:
                    s.sos_location = self._trigger_location
        except Exception:
            pass


# ================================================================
# 全局单例
# ================================================================

crash_detector: Optional[CrashDetector] = None


def get_crash_detector() -> Optional[CrashDetector]:
    return crash_detector
