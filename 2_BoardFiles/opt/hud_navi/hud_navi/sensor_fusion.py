"""
传感器融合定位 — EKF 融合 GPS + IMU + 视觉里程计
纯 Python 实现，无 numpy 依赖，适配 RK3568 2GB 内存约束

状态向量: [lat(rad), lon(rad), heading(rad), speed(m/s)]
控制输入: IMU [ax, ay, gz] (加速度+角速度)
测量更新: GPS [lat, lon] + 视觉 [heading]

GPS丢失后自动进入纯推测导航(dead reckoning), 最大持续60秒
"""
import math
import time
import logging
import threading
from collections import deque
from typing import Optional, Tuple

from config import (
    FUSION_OUTPUT_HZ, FUSION_GPS_WEIGHT, FUSION_IMU_WEIGHT, FUSION_VO_WEIGHT,
    FUSION_DEAD_RECKONING_MAX_SEC, FUSION_DEAD_RECKONING_DRIFT_M,
    FUSION_PROCESS_NOISE_POS, FUSION_PROCESS_NOISE_HDG, FUSION_PROCESS_NOISE_SPD,
    FUSION_MEASUREMENT_NOISE_GPS, FUSION_MEASUREMENT_NOISE_HDG,
    FUSION_GPS_TIMEOUT_SEC, FUSION_VO_TIMEOUT_SEC,
    IMU_SAMPLE_RATE,
)
from nav_state import state

logger = logging.getLogger(__name__)

# IMU 导入 (可选)
try:
    import smbus2
    _imu_available = True
except ImportError:
    _imu_available = False
    smbus2 = None


class SensorFusion:
    """
    扩展卡尔曼滤波器, 融合 GPS + IMU + 视觉里程计
    输出: 30Hz 平滑位置/航向/速度估计

    线程: 30Hz 守护线程, 内部维护 EKF 状态
    写 NavState: 1Hz (避免锁争用)
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # EKF 状态: [lat_rad, lon_rad, heading_rad, speed_ms]
        self._x = [0.0, 0.0, 0.0, 0.0]
        # 协方差矩阵 4×4 (初始化为大不确定性)
        self._P = [
            [100.0, 0.0, 0.0, 0.0],
            [0.0, 100.0, 0.0, 0.0],
            [0.0, 0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0, 10.0],
        ]
        # 过程噪声协方差
        self._Q = [
            [FUSION_PROCESS_NOISE_POS, 0, 0, 0],
            [0, FUSION_PROCESS_NOISE_POS, 0, 0],
            [0, 0, FUSION_PROCESS_NOISE_HDG, 0],
            [0, 0, 0, FUSION_PROCESS_NOISE_SPD],
        ]

        # IMU 读取器
        self._imu_reader: Optional['IMUReader'] = None
        if _imu_available:
            try:
                self._imu_reader = IMUReader()
                logger.info("IMU 传感器已初始化")
            except Exception as e:
                logger.warning(f"IMU 初始化失败: {e}, 降级为 GPS 模式")

        # 最新传感器数据缓冲
        self._latest_gps: Optional[Tuple[float, float, float, float]] = None  # lat,lon,hdg,spd
        self._latest_gps_time: float = 0.0
        self._latest_vo_heading: Optional[float] = None
        self._latest_vo_time: float = 0.0

        # 纯推测导航状态
        self._gps_loss_start: float = 0.0
        self._gps_is_lost: bool = False

        # 融合状态标志
        self._initialized: bool = False
        self._fusion_ready: bool = False

    # ================================================================
    # 公开接口
    # ================================================================

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._fusion_loop, daemon=True, name="Fusion"
        )
        self._thread.start()
        logger.info("传感器融合线程已启动 (30Hz)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._imu_reader:
            self._imu_reader.close()
        logger.info("传感器融合已停止")

    def push_gps(self, position: Optional[Tuple[float, float]],
                 heading: float, speed: float):
        """GPS线程回调: 推送最新GPS数据 (1Hz)"""
        if position is not None:
            self._latest_gps = (position[0], position[1], heading, speed)
            self._latest_gps_time = time.time()
            self._gps_is_lost = False

    def push_vo_heading(self, heading: float):
        """视觉里程计回调: 推送最新VO航向 (15Hz)"""
        self._latest_vo_heading = heading
        self._latest_vo_time = time.time()

    def get_fused_state(self) -> dict:
        """获取当前融合状态 (供外部查询)"""
        return {
            "position": (math.degrees(self._x[0]), math.degrees(self._x[1])),
            "heading": math.degrees(self._x[2]),
            "speed_ms": self._x[3],
            "speed_kmh": self._x[3] * 3.6,
            "confidence": self._compute_confidence(),
            "dead_reckoning": self._gps_is_lost,
            "initialized": self._initialized,
        }

    # ================================================================
    # EKF 核心算法
    # ================================================================

    def _fusion_loop(self):
        """主循环: 30Hz"""
        interval = 1.0 / FUSION_OUTPUT_HZ
        last_write = time.time()

        while self._running:
            t_start = time.time()

            # 1. 读取 IMU 数据 (如果有)
            imu = None
            if self._imu_reader:
                try:
                    imu = self._imu_reader.read()
                except Exception:
                    pass

            # 2. EKF 预测步 (由 IMU 驱动)
            if imu and self._initialized:
                dt = interval
                self._predict(imu, dt)

            # 3. EKF 更新步 (GPS)
            self._check_gps_health()
            if self._latest_gps and not self._gps_is_lost:
                if not self._initialized:
                    self._init_state(self._latest_gps)
                else:
                    self._update_gps()

            # 4. EKF 更新步 (视觉里程计)
            if self._latest_vo_heading and self._initialized:
                self._update_vo()

            # 5. 写入 NavState (降频到 1Hz)
            now = time.time()
            if now - last_write >= 1.0:
                self._write_to_state(now)
                last_write = now

            # 6. 帧率控制
            elapsed = time.time() - t_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _init_state(self, gps: Tuple[float, float, float, float]):
        """用首次GPS数据初始化EKF状态"""
        lat, lon, hdg, spd = gps
        self._x = [
            math.radians(lat),
            math.radians(lon),
            math.radians(hdg),
            spd / 3.6,  # km/h → m/s
        ]
        self._P = [
            [25.0, 0.0, 0.0, 0.0],
            [0.0, 25.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 5.0],
        ]
        self._initialized = True
        logger.info(f"EKF初始化: ({lat:.5f}, {lon:.5f}), 航向{hdg:.1f}°")

    def _predict(self, imu: dict, dt: float):
        """
        EKF 预测步
        使用 IMU 数据进行状态预测 (恒定速度 + 加速度模型)
        """
        ax = imu.get("ax", 0.0)  # m/s² (已转换)
        ay = imu.get("ay", 0.0)
        gz = imu.get("gz", 0.0)  # rad/s (已转换)

        # 状态预测
        # 将加速度从载体坐标系旋转到导航坐标系
        hdg = self._x[2]
        cos_h = math.cos(hdg)
        sin_h = math.sin(hdg)

        # 北向加速度 (载体x→北, 载体y→东)
        acc_north = ax * cos_h - ay * sin_h
        acc_east = ax * sin_h + ay * cos_h

        # 地球半径 (WGS84)
        R = 6378137.0
        lat_r = self._x[0]

        # 位置更新
        dt2 = 0.5 * dt * dt
        lat_new = self._x[0] + (self._x[3] * cos_h / R) * dt + (acc_north / R) * dt2
        lon_new = self._x[1] + (self._x[3] * sin_h / (R * math.cos(lat_r))) * dt + \
                  (acc_east / (R * math.cos(lat_r))) * dt2

        # 航向更新
        hdg_new = self._x[2] + gz * dt

        # 速度更新 (简化为标量)
        speed_new = self._x[3] + ax * dt  # 仅用前向加速度

        self._x = [lat_new, lon_new, hdg_new, speed_new]

        # 协方差预测 (简化: 对角线加过程噪声)
        for i in range(4):
            self._P[i][i] += self._Q[i][i] * dt

    def _update_gps(self):
        """EKF 更新步: GPS 位置测量"""
        if self._latest_gps is None:
            return

        gps_lat, gps_lon, gps_hdg, gps_spd = self._latest_gps

        # 测量残差 (innovation)
        z_lat = math.radians(gps_lat) - self._x[0]
        z_lon = math.radians(gps_lon) - self._x[1]
        z_hdg = math.radians(gps_hdg) - self._x[2]
        # 航向残差归一化到 [-pi, pi]
        z_hdg = math.atan2(math.sin(z_hdg), math.cos(z_hdg))

        # 简化的卡尔曼增益: K = P / (P + R)
        R_pos = FUSION_MEASUREMENT_NOISE_GPS  # 米, 转为弧度约 5m / 111320 ≈ 4.5e-5 rad
        R_pos_rad = (R_pos / 111320.0) ** 2
        R_hdg = math.radians(FUSION_MEASUREMENT_NOISE_HDG) ** 2

        # GPS 权重
        w = FUSION_GPS_WEIGHT

        # 位置更新
        K_pos = self._P[0][0] / (self._P[0][0] + R_pos_rad)
        self._x[0] += w * K_pos * z_lat
        self._x[1] += w * K_pos * z_lon
        self._P[0][0] *= (1 - w * K_pos)
        self._P[1][1] *= (1 - w * K_pos)

        # 航向更新
        K_hdg = self._P[2][2] / (self._P[2][2] + R_hdg)
        self._x[2] += w * K_hdg * z_hdg
        self._P[2][2] *= (1 - w * K_hdg)

        # 速度更新 (直接使用GPS速度)
        self._x[3] = 0.7 * self._x[3] + 0.3 * (gps_spd / 3.6)

    def _update_vo(self):
        """EKF 更新步: 视觉里程计航向测量"""
        if self._latest_vo_heading is None:
            return

        z_hdg = math.radians(self._latest_vo_heading) - self._x[2]
        z_hdg = math.atan2(math.sin(z_hdg), math.cos(z_hdg))

        R_hdg = math.radians(FUSION_MEASUREMENT_NOISE_HDG) ** 2
        w = FUSION_VO_WEIGHT

        K_hdg = self._P[2][2] / (self._P[2][2] + R_hdg)
        self._x[2] += w * K_hdg * z_hdg
        self._P[2][2] *= (1 - w * K_hdg)

    def _check_gps_health(self):
        """检查 GPS 信号健康状态"""
        now = time.time()
        gps_age = now - self._latest_gps_time if self._latest_gps_time > 0 else 999

        if gps_age > FUSION_GPS_TIMEOUT_SEC:
            if not self._gps_is_lost:
                self._gps_is_lost = True
                self._gps_loss_start = now
                logger.warning(f"GPS信号丢失, 进入纯推测导航 (最大{FUSION_DEAD_RECKONING_MAX_SEC}s)")
        else:
            self._gps_is_lost = False

    def _compute_confidence(self) -> float:
        """计算融合置信度 (0.0-1.0)"""
        if not self._initialized:
            return 0.0
        # 基于协方差迹和GPS状态
        trace = self._P[0][0] + self._P[1][1] + self._P[2][2] + self._P[3][3]
        base_conf = max(0.0, 1.0 - trace / 100.0)
        if self._gps_is_lost:
            elapsed = time.time() - self._gps_loss_start
            dr_factor = max(0.0, 1.0 - elapsed / FUSION_DEAD_RECKONING_MAX_SEC)
            return base_conf * 0.5 * dr_factor
        return base_conf

    # ================================================================
    # NavState 同步
    # ================================================================

    def _write_to_state(self, now: float):
        """将融合结果写入共享状态 (1Hz, 避免锁争用)"""
        if not self._initialized:
            return

        conf = self._compute_confidence()
        dr_remaining = 0.0
        if self._gps_is_lost:
            dr_remaining = max(0, FUSION_DEAD_RECKONING_MAX_SEC -
                               (now - self._gps_loss_start))

        with state as s:
            s.fused_position = (math.degrees(self._x[0]), math.degrees(self._x[1]))
            s.fused_heading = math.degrees(self._x[2])
            s.fusion_confidence = conf
            s.fusion_active = True
            s.dead_reckoning_active = self._gps_is_lost
            s.dead_reckoning_remaining = dr_remaining


# ================================================================
# IMU 读取器 (I2C)
# ================================================================

class IMUReader:
    """
    MPU6050 / ICM20948 六轴传感器 I2C 读取器
    纯 Python 实现, 使用 smbus2
    """

    # MPU6050 寄存器地址
    _REG_PWR_MGMT_1 = 0x6B
    _REG_ACCEL_CONFIG = 0x1C
    _REG_GYRO_CONFIG = 0x1B
    _REG_SMPLRT_DIV = 0x19
    _REG_ACCEL_XOUT_H = 0x3B
    _REG_GYRO_XOUT_H = 0x43

    def __init__(self, bus: int = None, addr: int = None):
        from config import IMU_I2C_BUS, IMU_I2C_ADDR, IMU_ACCEL_RANGE
        self._bus_num = bus if bus is not None else IMU_I2C_BUS
        self._addr = addr if addr is not None else IMU_I2C_ADDR
        self._bus = None

        # 加速度计量程 → 灵敏度 (LSB/g)
        accel_sensitivities = {2: 16384, 4: 8192, 8: 4096, 16: 2048}
        self._accel_scale = accel_sensitivities.get(IMU_ACCEL_RANGE, 4096)
        self._accel_range_reg = {2: 0, 4: 1, 8: 2, 16: 3}.get(IMU_ACCEL_RANGE, 2)

        # 陀螺仪量程 → 灵敏度 (LSB/dps)
        from config import IMU_GYRO_RANGE_DPS
        gyro_sensitivities = {250: 131, 500: 65.5, 1000: 32.8, 2000: 16.4}
        self._gyro_scale = gyro_sensitivities.get(IMU_GYRO_RANGE_DPS, 65.5)
        self._gyro_range_reg = {250: 0, 500: 1, 1000: 2, 2000: 3}.get(IMU_GYRO_RANGE_DPS, 1)

        # 重力加速度
        self._g = 9.80665

        self._init_i2c()

    def _init_i2c(self):
        """初始化 I2C 总线和 MPU6050 寄存器"""
        try:
            self._bus = smbus2.SMBus(self._bus_num)

            # 唤醒 MPU6050 (退出休眠模式)
            self._bus.write_byte_data(self._addr, self._REG_PWR_MGMT_1, 0x00)
            time.sleep(0.1)

            # 配置加速度计量程
            self._bus.write_byte_data(
                self._addr, self._REG_ACCEL_CONFIG,
                self._accel_range_reg << 3
            )

            # 配置陀螺仪量程
            self._bus.write_byte_data(
                self._addr, self._REG_GYRO_CONFIG,
                self._gyro_range_reg << 3
            )

            # 配置采样率分频器
            # 采样率 = 陀螺仪输出率(1kHz) / (1 + SMPLRT_DIV)
            from config import IMU_SAMPLE_RATE
            div = max(0, min(255, int(1000 / IMU_SAMPLE_RATE) - 1))
            self._bus.write_byte_data(self._addr, self._REG_SMPLRT_DIV, div)

            logger.info(f"MPU6050 I2C 初始化成功 (bus={self._bus_num}, addr=0x{self._addr:02X})")
        except Exception as e:
            logger.warning(f"MPU6050 I2C 初始化失败: {e}")
            if self._bus:
                try:
                    self._bus.close()
                except Exception:
                    pass
                self._bus = None
            raise

    def read(self) -> dict:
        """
        读取加速度计和陀螺仪原始值并转换为物理单位

        返回:
          {"ax": float, "ay": float, "az": float,   # 加速度 m/s²
           "gx": float, "gy": float, "gz": float,   # 角速度 rad/s
           "accel_mag": float}                        # 加速度幅值 g
        """
        if self._bus is None:
            return {"ax": 0, "ay": 0, "az": 0, "gx": 0, "gy": 0, "gz": 0, "accel_mag": 0}

        try:
            # 读取 14 字节: 加速度计(6) + 温度(2) + 陀螺仪(6)
            data = self._bus.read_i2c_block_data(self._addr, self._REG_ACCEL_XOUT_H, 14)

            # 加速度计原始值 (大端序, 有符号16位)
            ax_raw = self._s16(data[0], data[1])
            ay_raw = self._s16(data[2], data[3])
            az_raw = self._s16(data[4], data[5])

            # 陀螺仪原始值
            gx_raw = self._s16(data[8], data[9])
            gy_raw = self._s16(data[10], data[11])
            gz_raw = self._s16(data[12], data[13])

            # 转换为物理单位
            ax = ax_raw / self._accel_scale * self._g  # m/s²
            ay = ay_raw / self._accel_scale * self._g
            az = az_raw / self._accel_scale * self._g

            gx = gx_raw / self._gyro_scale * (math.pi / 180.0)  # rad/s
            gy = gy_raw / self._gyro_scale * (math.pi / 180.0)
            gz = gz_raw / self._gyro_scale * (math.pi / 180.0)

            # 加速度幅值 (g)
            accel_mag = math.sqrt(ax * ax + ay * ay + az * az) / self._g

            return {
                "ax": ax, "ay": ay, "az": az,
                "gx": gx, "gy": gy, "gz": gz,
                "accel_mag": accel_mag,
            }

        except Exception as e:
            logger.debug(f"IMU 读取异常: {e}")
            return {"ax": 0, "ay": 0, "az": 0, "gx": 0, "gy": 0, "gz": 0, "accel_mag": 0}

    @staticmethod
    def _s16(high: int, low: int) -> int:
        """两个字节合成有符号16位整数"""
        val = (high << 8) | low
        return val if val < 32768 else val - 65536

    def close(self):
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None


# ================================================================
# 全局单例 (供 main.py 使用)
# ================================================================

fusion_engine: Optional[SensorFusion] = None


def get_fusion_engine() -> Optional[SensorFusion]:
    return fusion_engine
