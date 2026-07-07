"""
线程安全的共享导航状态
所有模块通过此对象交换数据，避免竞态条件
"""
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any


@dataclass
class NavState:
    """
    导航核心状态，所有字段通过 lock 保护

    线程模型说明:
      - GPS线程: 写入 current_position, gps_quality
      - 蓝牙线程: 写入 destination
      - 规划线程: 写入 route, instruction, remaining_distance
      - 显示线程: 只读所有字段
    """

    # ---- GPS 数据 ----
    current_position: Optional[Tuple[float, float]] = None  # (lat, lon)
    gps_speed: float = 0.0  # km/h
    gps_heading: float = 0.0  # 航向角(度), 0=正北
    gps_quality: Dict[str, Any] = field(default_factory=lambda: {
        "satellites": 0,
        "hdop": 99.9,
        "fix_quality": 0,  # 0=无定位, 1=GPS, 2=DGPS
        "signal_weak": True,
    })

    # ---- 目的地 ----
    destination: Optional[Tuple[float, float]] = None  # (lat, lon) or None
    destination_name: str = ""

    # ---- 路线 ----
    route: List[Tuple[float, float]] = field(default_factory=list)  # [(lat,lon), ...]
    route_steps: List[Dict[str, Any]] = field(default_factory=list)  # Turn-by-Turn步骤
    route_mode: str = "auto"  # "online" / "offline"

    # ---- 导航指令 ----
    instruction: str = ""  # 当前导航指令文本
    instruction_distance: float = 0.0  # 到下个路口距离(米)
    turn_direction: str = "straight"  # "left" / "right" / "straight" / "uturn" / "arrived"
    remaining_distance: float = 0.0  # 剩余总距离(米)
    eta_minutes: float = 0.0  # 预计到达时间(分钟)

    # ---- 导航状态 ----
    is_navigating: bool = False
    is_arrived: bool = False
    is_off_route: bool = False

    # ---- 道路安全 ----
    road_condition: str = ""       # "🟢安全" / "🟡注意" / "🔴危险"
    road_summary: str = ""         # "🟡当前主干道 | 🟢前方300m自行车道"
    road_upcoming: list = field(default_factory=list)  # [{road, level, distance}]

    # ---- 语音命令 ----
    voice_last_cmd: str = ""
    voice_last_raw: str = ""
    voice_last_time: float = 0.0

    # ---- AI 摄像头 (V2.0) ----
    camera_active: bool = False       # 摄像头是否运行中
    camera_alert_level: int = 0       # 预警等级 0=无 1=观察 2=警告 3=危险
    camera_alert_msg: str = ""        # 预警文本
    camera_vehicles: int = 0          # 检测到的车辆数

    # ---- 系统状态 ----
    battery_level: float = 100.0  # 电量百分比
    is_charging: bool = False
    online_mode: bool = True
    error_message: str = ""


class SharedState:
    """
    线程安全的状态管理器
    用法:
        state = SharedState()
        with state as s:
            s.current_position = (39.9, 116.4)
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._state = NavState()

    def __enter__(self) -> NavState:
        self._lock.acquire()
        return self._state

    def __exit__(self, *args):
        self._lock.release()

    def get_snapshot(self) -> NavState:
        """获取状态快照（用于显示线程读取，不持有锁太久）"""
        with self:
            # 浅拷贝足够，因为所有字段都是不可变类型
            snapshot = NavState()
            snapshot.current_position = self._state.current_position
            snapshot.gps_speed = self._state.gps_speed
            snapshot.gps_heading = self._state.gps_heading
            snapshot.gps_quality = dict(self._state.gps_quality)
            snapshot.destination = self._state.destination
            snapshot.destination_name = self._state.destination_name
            snapshot.route = list(self._state.route)
            snapshot.route_steps = list(self._state.route_steps)
            snapshot.route_mode = self._state.route_mode
            snapshot.instruction = self._state.instruction
            snapshot.instruction_distance = self._state.instruction_distance
            snapshot.turn_direction = self._state.turn_direction
            snapshot.remaining_distance = self._state.remaining_distance
            snapshot.eta_minutes = self._state.eta_minutes
            snapshot.is_navigating = self._state.is_navigating
            snapshot.is_arrived = self._state.is_arrived
            snapshot.is_off_route = self._state.is_off_route
            snapshot.road_condition = self._state.road_condition
            snapshot.road_summary = self._state.road_summary
            snapshot.road_upcoming = list(self._state.road_upcoming)
            snapshot.voice_last_cmd = self._state.voice_last_cmd
            snapshot.voice_last_raw = self._state.voice_last_raw
            snapshot.voice_last_time = self._state.voice_last_time
            snapshot.camera_active = self._state.camera_active
            snapshot.camera_alert_level = self._state.camera_alert_level
            snapshot.camera_alert_msg = self._state.camera_alert_msg
            snapshot.camera_vehicles = self._state.camera_vehicles
            snapshot.battery_level = self._state.battery_level
            snapshot.is_charging = self._state.is_charging
            snapshot.online_mode = self._state.online_mode
            snapshot.error_message = self._state.error_message
            return snapshot


# 全局单例
state = SharedState()
