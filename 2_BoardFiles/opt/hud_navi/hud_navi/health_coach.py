"""
骑行健康教练 — BLE 心率监测 + 5区训练模型 + 卡路里 + 补水提醒

BLE 心率服务 (标准 Heart Rate Profile):
  Service UUID: 0x180D
  Characteristic: 0x2A37 (Heart Rate Measurement) — Notify
  Characteristic: 0x2A38 (Body Sensor Location) — Read

5区心率训练模型:
  区间1 热身: HR < 60% MaxHR — 蓝色
  区间2 燃脂: HR 60-70% MaxHR — 绿色
  区间3 有氧: HR 70-80% MaxHR — 黄色
  区间4 无氧: HR 80-90% MaxHR — 橙色
  区间5 极限: HR > 90% MaxHR — 红色

卡路里公式: kcal/min = MET × 体重_kg × 3.5 / 200
补水提醒: 基础20分钟 + 温度/心率动态调整
"""
import os
import time
import math
import json
import logging
import threading
from collections import deque
from typing import Optional, Dict, Any

from config import (
    BLE_HRM_SERVICE_UUID, BLE_HRM_CHAR_UUID, BLE_HRM_BODY_SENSOR_UUID,
    RIDER_WEIGHT_KG, RIDER_AGE, RIDER_MAX_HR, RIDER_GENDER,
    HR_SCAN_TIMEOUT_SEC, HR_RECONNECT_INTERVAL_SEC, HR_NOTIFY_TIMEOUT_SEC,
    HR_ZONE_WARMUP_MAX, HR_ZONE_FATBURN_MAX, HR_ZONE_AEROBIC_MAX,
    HR_ZONE_ANAEROBIC_MAX,
    HYDRATION_INTERVAL_BASE_SEC, HYDRATION_TEMP_ADJUST_C,
    HYDRATION_TEMP_SPEEDUP, HYDRATION_HR_BOOST_THRESHOLD, HYDRATION_HR_SPEEDUP,
    CYCLING_PEAK_MET, CYCLING_CRUISE_MET, CYCLING_EASY_MET,
    CALORIE_UPDATE_INTERVAL_SEC,
)
from nav_state import state

logger = logging.getLogger(__name__)

# D-Bus / BLE 可选导入
try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    _dbus_available = True
except ImportError:
    _dbus_available = False

# HR 区间元数据
HR_ZONES = {
    0: {"name": "未知", "color": (160, 160, 160), "min_pct": 0.0, "max_pct": 0.0},
    1: {"name": "热身", "color": (100, 149, 237), "min_pct": 0.0, "max_pct": 0.60},
    2: {"name": "燃脂", "color": (0, 220, 80),   "min_pct": 0.60, "max_pct": 0.70},
    3: {"name": "有氧", "color": (255, 185, 15),  "min_pct": 0.70, "max_pct": 0.80},
    4: {"name": "无氧", "color": (255, 100, 0),   "min_pct": 0.80, "max_pct": 0.90},
    5: {"name": "极限", "color": (255, 60, 60),   "min_pct": 0.90, "max_pct": 1.0},
}


class HealthCoach:
    """
    骑行健康教练

    线程: 1Hz 守护线程
    功能: BLE HRM 连接 + 心率区间计算 + 卡路里估算 + 补水提醒
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 骑手参数
        self._weight_kg = RIDER_WEIGHT_KG
        self._age = RIDER_AGE
        self._max_hr = RIDER_MAX_HR if RIDER_MAX_HR > 0 else (220 - RIDER_AGE)
        self._gender = RIDER_GENDER

        # 心率数据
        self._current_hr: int = 0
        self._hr_history: deque = deque(maxlen=300)  # 最近5分钟 @1Hz
        self._hr_device_name: str = ""
        self._hr_connected: bool = False
        self._last_hr_time: float = 0.0

        # 卡路里
        self._total_calories: float = 0.0
        self._last_calorie_update: float = 0.0
        self._calorie_rate: float = 0.0

        # 补水
        self._last_hydration_time: float = 0.0
        self._hydration_interval: int = HYDRATION_INTERVAL_BASE_SEC

        # BLE 连接管理
        self._ble_adapter = None
        self._ble_device = None
        self._ble_char = None
        self._reconnect_timer: float = 0.0

        # 骑行开始时间
        self._ride_start_time: Optional[float] = None

        # 尝试初始化 BLE (可能在无 D-Bus 环境中降级)
        self._ble_ok = _dbus_available
        if not _dbus_available:
            logger.warning("BLE D-Bus 不可用, 健康教练将在无HR模式下运行")

        logger.info(f"健康教练就绪 (最大心率={self._max_hr}bpm, "
                    f"体重={self._weight_kg}kg, BLE={'可用' if self._ble_ok else '降级'})")

    # ================================================================
    # 公开接口
    # ================================================================

    def start(self):
        if self._running:
            return
        self._running = True
        self._ride_start_time = time.time()
        self._last_hydration_time = time.time()
        self._thread = threading.Thread(
            target=self._coach_loop, daemon=True, name="Health"
        )
        self._thread.start()
        logger.info("健康教练线程已启动 (1Hz)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._disconnect_hrm()
        # 骑行后建议
        advice = self._generate_recovery_advice()
        if advice:
            logger.info(f"骑行恢复建议: {advice}")
        logger.info("健康教练已停止")

    def get_health_status(self) -> dict:
        """获取健康状态 (HTTP API 用)"""
        return {
            "heart_rate": self._current_hr,
            "hr_zone": self._compute_hr_zone(self._current_hr),
            "hr_zone_name": self._get_zone_name(self._compute_hr_zone(self._current_hr)),
            "hr_connected": self._hr_connected,
            "hr_device": self._hr_device_name,
            "max_hr": self._max_hr,
            "calories_burned": round(self._total_calories, 1),
            "calorie_rate": round(self._calorie_rate, 1),
            "hydration_reminder": self._check_hydration(),
            "hydration_due_sec": self._get_hydration_due_sec(),
            "weight_kg": self._weight_kg,
            "age": self._age,
        }

    def update_settings(self, weight_kg: float = None, age: int = None,
                        max_hr: int = None, gender: str = None):
        """更新骑手参数 (HTTP API 用)"""
        if weight_kg is not None and weight_kg > 0:
            self._weight_kg = weight_kg
        if age is not None and age > 0:
            self._age = age
            if max_hr is None:
                self._max_hr = 220 - age
        if max_hr is not None and max_hr > 0:
            self._max_hr = max_hr
        if gender is not None:
            self._gender = gender
        logger.info(f"骑手参数已更新: {self._weight_kg}kg, {self._age}岁, "
                    f"最大HR={self._max_hr}")

    # ================================================================
    # 主循环 (1Hz)
    # ================================================================

    def _coach_loop(self):
        """主循环: 1Hz"""
        while self._running:
            try:
                # 1. BLE HRM 管理
                if self._ble_ok:
                    if not self._hr_connected:
                        self._try_connect_hrm()
                    else:
                        self._check_hrm_timeout()

                # 2. 计算心率区间
                zone = self._compute_hr_zone(self._current_hr)
                zone_name = self._get_zone_name(zone)

                # 3. 卡路里更新 (每 CALORIE_UPDATE_INTERVAL_SEC 秒)
                now = time.time()
                if now - self._last_calorie_update >= CALORIE_UPDATE_INTERVAL_SEC:
                    self._update_calories(now)

                # 4. 补水检查
                hydration = self._check_hydration()

                # 5. 写入 NavState
                self._write_to_state(zone, zone_name, hydration)

            except Exception as e:
                logger.debug(f"健康教练循环异常: {e}")

            time.sleep(1.0)

    # ================================================================
    # BLE HRM 连接管理
    # ================================================================

    def _try_connect_hrm(self):
        """尝试扫描并连接 BLE 心率设备"""
        now = time.time()
        if now - self._reconnect_timer < HR_RECONNECT_INTERVAL_SEC:
            return

        self._reconnect_timer = now
        logger.info("正在扫描 BLE 心率设备...")

        try:
            # 使用 busctl 扫描 (兼容 dbus-next 不可用的情况)
            import subprocess
            result = subprocess.run(
                ["busctl", "call", "org.bluez", "/org/bluez/hci0",
                 "org.bluez.Adapter1", "StartDiscovery"],
                capture_output=True, timeout=5
            )

            # 检查已知设备 (简化方案: 扫描后通过 hcitool 查找)
            devices_result = subprocess.run(
                ["hcitool", "lescan", "--duration", str(HR_SCAN_TIMEOUT_SEC)],
                capture_output=True, timeout=HR_SCAN_TIMEOUT_SEC + 2
            )

            # 简单实现: 标记为已连接 (模拟)
            # 完整实现需要 D-Bus ObjectManager 监听和 GATT 连接
            # 这里提供接口框架，实际调试时需要配合 BlueZ 5 具体版本调整
            logger.debug("BLE HRM 扫描完成 (需实际硬件调试)")

        except FileNotFoundError:
            logger.warning("busctl/hcitool 不可用, BLE HRM 需要 BlueZ 工具")
        except Exception as e:
            logger.debug(f"BLE HRM 扫描失败: {e}")

    def _check_hrm_timeout(self):
        """检查 HRM 通知超时"""
        if time.time() - self._last_hr_time > HR_NOTIFY_TIMEOUT_SEC:
            logger.warning("心率通知超时, 标记为断开")
            self._hr_connected = False
            self._current_hr = 0

    def _disconnect_hrm(self):
        """断开 HRM 连接"""
        self._hr_connected = False
        self._current_hr = 0
        logger.info("HRM 已断开")

    def _on_hr_notification(self, hr_value: int):
        """心率通知回调 (由 BLE 线程调用)"""
        self._current_hr = hr_value
        self._last_hr_time = time.time()
        self._hr_history.append({"t": time.time(), "hr": hr_value})

    # ================================================================
    # 心率区间计算
    # ================================================================

    def _compute_hr_zone(self, hr: int) -> int:
        """
        计算心率训练区间

        返回:
          0=未知(HR=0), 1=热身, 2=燃脂, 3=有氧, 4=无氧, 5=极限
        """
        if hr <= 0 or self._max_hr <= 0:
            return 0

        pct = hr / self._max_hr

        if pct >= HR_ZONE_ANAEROBIC_MAX:   # >90%
            return 5
        elif pct >= HR_ZONE_AEROBIC_MAX:   # 80-90%
            return 4
        elif pct >= HR_ZONE_FATBURN_MAX:   # 70-80%
            return 3
        elif pct >= HR_ZONE_WARMUP_MAX:    # 60-70%
            return 2
        else:                               # <60%
            return 1

    @staticmethod
    def _get_zone_name(zone: int) -> str:
        return HR_ZONES.get(zone, {}).get("name", "未知")

    # ================================================================
    # 卡路里计算
    # ================================================================

    def _update_calories(self, now: float):
        """
        更新累计卡路里

        公式: kcal/min = MET × 体重_kg × 3.5 / 200

        MET 值根据心率动态调整:
          HR区间1 → MET=3.5 (休闲)
          HR区间2 → MET=5.0 (巡航)
          HR区间3 → MET=6.5 (中等)
          HR区间4 → MET=7.5 (高强度)
          HR区间5 → MET=8.0 (极限)
        """
        # 获取当前速度用于 MET 调整
        snap = state.get_snapshot()
        speed = snap.gps_speed  # km/h

        # 基于心率的 MET
        zone = self._compute_hr_zone(self._current_hr)
        zone_met = {
            0: CYCLING_EASY_MET,
            1: CYCLING_EASY_MET,
            2: CYCLING_CRUISE_MET,
            3: (CYCLING_CRUISE_MET + CYCLING_PEAK_MET) / 2,
            4: CYCLING_PEAK_MET * 0.9,
            5: CYCLING_PEAK_MET,
        }
        hr_met = zone_met.get(zone, CYCLING_CRUISE_MET)

        # 基于速度的 MET (速度越快 MET 越高)
        if speed > 25:
            speed_met = CYCLING_PEAK_MET
        elif speed > 18:
            speed_met = CYCLING_PEAK_MET * 0.85
        elif speed > 12:
            speed_met = CYCLING_CRUISE_MET
        else:
            speed_met = CYCLING_EASY_MET

        # 综合 MET (心率优先，速度为辅)
        met = hr_met if self._current_hr > 0 else speed_met

        # 性别调整
        if self._gender == "female":
            met *= 0.9

        # kcal/min = MET × kg × 3.5 / 200
        kcal_per_min = met * self._weight_kg * 3.5 / 200.0

        # 累计
        dt = now - self._last_calorie_update
        self._total_calories += kcal_per_min * (dt / 60.0)
        self._calorie_rate = kcal_per_min
        self._last_calorie_update = now

    # ================================================================
    # 补水提醒
    # ================================================================

    def _check_hydration(self) -> bool:
        """
        检查是否需要补水提醒

        基础间隔: 20分钟
        高温调整: 温度 > 25°C → 间隔缩短 30%
        高心率调整: HR > 140 → 间隔缩短 20%
        """
        now = time.time()

        # 动态间隔计算
        interval = float(HYDRATION_INTERVAL_BASE_SEC)

        # 温度调整
        snap = state.get_snapshot()
        temp = snap.ambient_temperature if snap.ambient_temperature > 0 else 25.0
        if temp > HYDRATION_TEMP_ADJUST_C:
            interval *= (1.0 - HYDRATION_TEMP_SPEEDUP)  # 缩短30%

        # 心率调整
        if self._current_hr > HYDRATION_HR_BOOST_THRESHOLD:
            interval *= (1.0 - HYDRATION_HR_SPEEDUP)  # 缩短20%

        self._hydration_interval = int(interval)

        due = (now - self._last_hydration_time) >= interval

        if due:
            self._last_hydration_time = now  # 自动重置

        return due

    def _get_hydration_due_sec(self) -> int:
        """距离下次补水剩余秒数"""
        elapsed = time.time() - self._last_hydration_time
        return max(0, self._hydration_interval - int(elapsed))

    # ================================================================
    # 恢复建议
    # ================================================================

    def _generate_recovery_advice(self) -> dict:
        """生成骑行后恢复建议"""
        if self._ride_start_time is None:
            return {}

        duration_min = (time.time() - self._ride_start_time) / 60

        # 平均心率
        hr_values = [r["hr"] for r in self._hr_history if r["hr"] > 0]
        avg_hr = sum(hr_values) / len(hr_values) if hr_values else 0

        # 区间分布
        zone_counts = {i: 0 for i in range(1, 6)}
        for hr in hr_values:
            z = self._compute_hr_zone(hr)
            if z > 0:
                zone_counts[z] = zone_counts.get(z, 0) + 1

        # 补水统计
        missed_hydration = max(0, int(duration_min * 60 / HYDRATION_INTERVAL_BASE_SEC))

        advice = {
            "duration_min": round(duration_min, 1),
            "avg_hr": round(avg_hr, 1),
            "max_hr": max(hr_values) if hr_values else 0,
            "total_cal": round(self._total_calories, 0),
            "zone_distribution": zone_counts,
            "hydration_reminders": missed_hydration,
            "recommendation": "",
        }

        # 生成建议
        if avg_hr > self._max_hr * 0.75:
            advice["recommendation"] += "本次骑行强度较高, 建议充分休息24小时。"
        elif avg_hr > self._max_hr * 0.60:
            advice["recommendation"] += "中等强度骑行, 注意补充蛋白质。"
        else:
            advice["recommendation"] += "轻量骑行, 可作为恢复训练。"

        if self._total_calories > 500:
            advice["recommendation"] += f" 消耗{self._total_calories:.0f}千卡, 建议补充碳水+电解质。"
        if missed_hydration > 0:
            advice["recommendation"] += f" 建议补充{missed_hydration * 200}ml水分。"

        return advice

    # ================================================================
    # NavState 同步
    # ================================================================

    def _write_to_state(self, zone: int, zone_name: str, hydration: bool):
        """写入 NavState"""
        try:
            with state as s:
                s.heart_rate = self._current_hr
                s.hr_zone = zone
                s.hr_zone_name = zone_name
                s.hr_connected = self._hr_connected
                s.hr_device_name = self._hr_device_name
                s.calories_burned = round(self._total_calories, 1)
                s.hydration_reminder = hydration
                s.hydration_due_sec = self._get_hydration_due_sec()
        except Exception as e:
            logger.debug(f"写入健康状态失败: {e}")


# ============================================================
# 全局单例
# ============================================================

health_coach: Optional[HealthCoach] = None


def get_health_coach() -> Optional[HealthCoach]:
    return health_coach
