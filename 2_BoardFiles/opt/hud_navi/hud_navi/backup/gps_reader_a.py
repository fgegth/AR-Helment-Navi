"""
GPS模块 — 串口读取 + NMEA 0183 解析
负责人: A
"""
import time
import logging
import threading
from typing import Optional, Tuple, Dict, Any

import serial

from config import (
    GPS_SERIAL_PORT, GPS_BAUDRATE, GPS_TIMEOUT,
    WEAK_GPS_SIGNAL_THRESHOLD,
)
from nav_state import state

logger = logging.getLogger(__name__)


class GPSReader:
    """
    GPS串口读取器
    运行在独立线程中，以 1Hz 频率更新共享状态
    """

    def __init__(self):
        self._serial: Optional[serial.Serial] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # NMEA 解析缓存
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self._speed_kmh: float = 0.0
        self._heading: float = 0.0
        self._satellites: int = 0
        self._hdop: float = 99.9
        self._fix_quality: int = 0

    def open(self) -> bool:
        """打开GPS串口，返回是否成功"""
        try:
            self._serial = serial.Serial(
                port=GPS_SERIAL_PORT,
                baudrate=GPS_BAUDRATE,
                timeout=GPS_TIMEOUT,
            )
            logger.info(f"GPS串口已打开: {GPS_SERIAL_PORT} @ {GPS_BAUDRATE}")
            return True
        except serial.SerialException as e:
            logger.error(f"GPS串口打开失败: {e}")
            return False

    def start(self):
        """启动GPS读取线程"""
        if self._serial is None or not self._serial.is_open:
            if not self.open():
                logger.warning("GPS串口不可用，GPS数据将无法更新")
                return

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name="GPS-Thread")
        self._thread.start()
        logger.info("GPS读取线程已启动")

    def stop(self):
        """停止GPS读取"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("GPS串口已关闭")

    def _read_loop(self):
        """主循环：持续读取NMEA语句并解析"""
        buf = ""
        while self._running:
            try:
                if self._serial.in_waiting:
                    raw = self._serial.read(self._serial.in_waiting).decode("ascii", errors="ignore")
                    buf += raw

                    # 按行分割，处理完整语句
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if line:
                            self._parse_nmea(line)

                # 每隔1秒更新一次共享状态
                self._update_state()

            except serial.SerialException as e:
                logger.error(f"GPS串口读取错误: {e}")
                time.sleep(1)
            except Exception as e:
                logger.exception(f"GPS解析异常: {e}")
                time.sleep(1)

    def _parse_nmea(self, sentence: str):
        """解析单条NMEA 0183语句"""
        if not sentence.startswith("$"):
            return

        # 校验和验证
        if "*" in sentence:
            data, checksum_str = sentence.rsplit("*", 1)
            try:
                expected = int(checksum_str, 16)
                calculated = 0
                for ch in data[1:]:  # 跳过 '$'
                    calculated ^= ord(ch)
                if calculated != expected:
                    return  # 校验失败，丢弃
            except ValueError:
                return
        else:
            data = sentence

        fields = data.split(",")
        talker = fields[0]

        try:
            if talker.endswith("RMC"):  # $GPRMC / $GNRMC — 推荐最小定位信息
                self._parse_rmc(fields)
            elif talker.endswith("GGA"):  # $GPGGA / $GNGGA — 定位信息
                self._parse_gga(fields)
            elif talker.endswith("GSV"):  # $GPGSV / $GNGSV — 可见卫星
                self._parse_gsv(fields)
        except (IndexError, ValueError):
            pass  # 字段不全，跳过

    def _parse_rmc(self, fields: list):
        """
        $GPRMC,hhmmss.ss,A,llll.ll,N,yyyyy.yy,E,spd,hdg,date,,,status*CS
          [1] 时间 UTC hhmmss.ss
          [2] 状态 A=有效 V=无效
          [3] 纬度 ddmm.mmmm
          [4] 纬度半球 N/S
          [5] 经度 dddmm.mmmm
          [6] 经度半球 E/W
          [7] 速度(节)
          [8] 航向(度)
        """
        if len(fields) < 9:
            return
        if fields[2] != "A":
            return  # 定位无效

        # 解析纬度: ddmm.mmmm → 十进制
        lat_raw = fields[3]
        lat_deg = int(lat_raw[:2])
        lat_min = float(lat_raw[2:])
        self._lat = lat_deg + lat_min / 60.0
        if fields[4] == "S":
            self._lat = -self._lat

        # 解析经度: dddmm.mmmm → 十进制
        lon_raw = fields[5]
        lon_deg = int(lon_raw[:3])
        lon_min = float(lon_raw[3:])
        self._lon = lon_deg + lon_min / 60.0
        if fields[6] == "W":
            self._lon = -self._lon

        # 速度(节 → km/h)
        if fields[7]:
            self._speed_kmh = float(fields[7]) * 1.852

        # 航向
        if fields[8]:
            self._heading = float(fields[8])

    def _parse_gga(self, fields: list):
        """
        $GPGGA,hhmmss.ss,llll.ll,N,yyyyy.yy,E,q,nn,hdop,alt,M,...*CS
          [6] 定位质量: 0=无效 1=GPS 2=DGPS
          [7] 卫星数
          [8] HDOP
        """
        if len(fields) < 9:
            return
        self._fix_quality = int(fields[6]) if fields[6] else 0
        self._satellites = int(fields[7]) if fields[7] else 0
        self._hdop = float(fields[8]) if fields[8] else 99.9

    def _parse_gsv(self, fields: list):
        """$GPGSV — 可见卫星信息（只取总数，不逐颗解析）"""
        # fields[3] 是可见卫星总数
        if len(fields) >= 4 and fields[3]:
            self._satellites = int(fields[3])

    def _update_state(self):
        """将解析结果写入共享状态"""
        if self._lat is None or self._lon is None:
            return

        signal_weak = (
            self._satellites < WEAK_GPS_SIGNAL_THRESHOLD
            or self._fix_quality == 0
        )

        with state as s:
            s.current_position = (round(self._lat, 7), round(self._lon, 7))
            s.gps_speed = round(self._speed_kmh, 2)
            s.gps_heading = round(self._heading, 1)
            s.gps_quality = {
                "satellites": self._satellites,
                "hdop": self._hdop,
                "fix_quality": self._fix_quality,
                "signal_weak": signal_weak,
            }

    @property
    def has_fix(self) -> bool:
        return self._fix_quality > 0 and self._lat is not None


# ============================================================
# 工具函数：Haversine 距离计算
# ============================================================
import math


def haversine_distance(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> float:
    """
    计算两点间的大圆距离(米)
    用于偏航检测、到达判定等
    """
    R = 6371000  # 地球半径(米)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def bearing_angle(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> float:
    """
    计算从点1到点2的方位角(0~360°, 0=正北)
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    theta = math.degrees(math.atan2(y, x))
    return (theta + 360) % 360
