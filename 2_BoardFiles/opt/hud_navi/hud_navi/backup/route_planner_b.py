"""
路径规划与导航引擎
负责人: B

职责:
  1. 偏航检测 — 判断当前位置是否偏离规划路线
  2. 导航指令生成 — 根据位置和路线生成转向提示
  3. 到达判定 — 检测是否已到达目的地
  4. 路线管理 — 调用在线/离线Provider获取路线
"""
import logging
import math
from typing import List, Tuple, Optional

from gps_reader_a import haversine_distance, bearing_angle
from config import (
    OFF_ROUTE_THRESHOLD_M,
    OFF_ROUTE_CONSECUTIVE_COUNT,
    DESTINATION_ARRIVED_THRESHOLD_M,
    VOICE_ALERT_DISTANCE_M,
    ROUTE_MODE,
)

logger = logging.getLogger(__name__)


class RoutePlanner:
    """
    导航引擎
    不关心路线来源（在线或离线），只处理路线上的逻辑
    """

    def __init__(self):
        self._off_route_counter: int = 0  # 连续偏航计数
        self._last_instruction: str = ""
        self._voice_alerted: bool = False  # 当前路口是否已播报

    def is_off_route(
        self,
        current_pos: Tuple[float, float],
        route: List[Tuple[float, float]],
    ) -> bool:
        """
        偏航检测
        计算当前点到路线每一段的最短距离，取最小值
        连续N次超过阈值才判定为偏航（防GPS漂移）
        """
        if not route or len(route) < 2:
            return False

        min_distance = self._point_to_polyline_distance(current_pos, route)

        if min_distance > OFF_ROUTE_THRESHOLD_M:
            self._off_route_counter += 1
            logger.debug(f"偏航计数: {self._off_route_counter}/{OFF_ROUTE_CONSECUTIVE_COUNT}, "
                         f"距离路线: {min_distance:.0f}m")
            if self._off_route_counter >= OFF_ROUTE_CONSECUTIVE_COUNT:
                return True
        else:
            self._off_route_counter = 0

        return False

    def reset_off_route_counter(self):
        """重置偏航计数（路线更新后调用）"""
        self._off_route_counter = 0

    def is_arrived(
        self,
        current_pos: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> bool:
        """检查是否已到达目的地"""
        if current_pos is None or destination is None:
            return False
        dist = haversine_distance(
            current_pos[0], current_pos[1],
            destination[0], destination[1],
        )
        return dist < DESTINATION_ARRIVED_THRESHOLD_M

    def get_next_instruction(
        self,
        current_pos: Tuple[float, float],
        route: List[Tuple[float, float]],
        heading: float,
    ) -> dict:
        """
        生成导航指令

        算法:
          1. 找到路线上距离当前位置最近的点(closest_index)
          2. 从该点向前搜索，找到第一个方位角变化>30°的节点(turn_index)
          3. 计算从当前位置沿路线到turn_index的距离
          4. 计算turn_index处的转角方向(左/右/直行/掉头)
          5. 返回自然语言指令

        返回:
          {
            "instruction": "前方200米右转",
            "distance": 200.0,       # 到路口距离(米)
            "direction": "right",    # left/right/straight/uturn/arrived
            "should_voice": True,    # 是否需要语音播报
          }
        """
        if not route or len(route) < 2:
            return self._make_instruction("等待路线规划...", 0, "straight", False)

        # 1. 找最近点
        closest_idx = self._find_closest_point(current_pos, route)

        # 2. 如果是最末尾，说明快到终点了
        if closest_idx >= len(route) - 2:
            return self._make_instruction("即将到达目的地", 0, "arrived", False)

        # 3. 向前搜索转弯点
        turn_idx, turn_angle, cumulative_dist = self._find_next_turn(
            route, closest_idx, current_pos
        )

        # 4. 判断方向
        if turn_idx is None:
            # 没有明显转弯，直行
            return self._make_instruction("沿当前道路直行", 0, "straight", False)

        # 5. 生成指令
        direction = self._angle_to_direction(turn_angle)
        distance_text = self._format_distance(cumulative_dist)

        if direction == "straight":
            text = f"沿当前道路直行"
        elif direction == "uturn":
            text = f"{distance_text}掉头"
        else:
            dir_cn = {"left": "左转", "right": "右转"}
            text = f"{distance_text}{dir_cn.get(direction, direction)}"

        # 6. 判断是否需要语音播报
        should_voice = (
            cumulative_dist < VOICE_ALERT_DISTANCE_M
            and not self._voice_alerted
            and cumulative_dist > 0
        )

        if should_voice:
            self._voice_alerted = True

        return self._make_instruction(text, cumulative_dist, direction, should_voice)

    def reset_voice_alert(self):
        """重置语音播报标志（路口通过后调用）"""
        self._voice_alerted = False

    def get_remaining_info(
        self,
        current_pos: Tuple[float, float],
        route: List[Tuple[float, float]],
        closest_idx: int = None,
    ) -> Tuple[float, float]:
        """
        计算剩余距离(米)和预计时间(分钟)
        时间按平均骑行速度15km/h估算
        """
        if not route:
            return 0.0, 0.0

        if closest_idx is None:
            closest_idx = self._find_closest_point(current_pos, route)

        # 从最近点沿路线到终点的累计距离
        remaining = 0.0
        for i in range(closest_idx, len(route) - 1):
            remaining += haversine_distance(
                route[i][0], route[i][1],
                route[i + 1][0], route[i + 1][1],
            )

        avg_speed = 15.0  # km/h 骑行平均速度
        eta = (remaining / 1000.0) / avg_speed * 60.0  # 分钟

        return remaining, eta

    # ================================================================
    # 内部算法
    # ================================================================

    def _point_to_polyline_distance(
        self,
        point: Tuple[float, float],
        polyline: List[Tuple[float, float]],
    ) -> float:
        """
        计算点到折线的最短距离(米)
        对每个线段计算垂足距离，取最小值
        """
        min_dist = float("inf")

        for i in range(len(polyline) - 1):
            seg_start = polyline[i]
            seg_end = polyline[i + 1]

            # 线段长度
            seg_len = haversine_distance(
                seg_start[0], seg_start[1],
                seg_end[0], seg_end[1],
            )

            if seg_len < 1e-6:
                # 线段退化为点
                d = haversine_distance(
                    point[0], point[1],
                    seg_start[0], seg_start[1],
                )
            else:
                # 点到线段的垂足（用平面近似，因为距离很短）
                d = self._point_to_segment_distance_approx(point, seg_start, seg_end)

            if d < min_dist:
                min_dist = d

        return min_dist

    def _point_to_segment_distance_approx(
        self,
        point: Tuple[float, float],
        a: Tuple[float, float],
        b: Tuple[float, float],
    ) -> float:
        """
        点到线段的最短距离（平面近似）
        将经纬度近似转换为米坐标后计算
        """
        # 转换到以点a为原点的局部米坐标
        lat0, lon0 = a

        def to_meters(pt):
            lat, lon = pt
            dy = (lat - lat0) * 111320.0  # 纬度→米
            dx = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))  # 经度→米
            return (dx, dy)

        px, py = to_meters(point)
        ax, ay = to_meters(a)  # (0, 0)
        bx, by = to_meters(b)

        # 向量 AB
        abx = bx - ax
        aby = by - ay

        if abx * abx + aby * aby < 1e-6:
            return math.sqrt(px * px + py * py)

        # 投影参数 t
        t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / (abx * abx + aby * aby)))

        # 垂足坐标
        proj_x = ax + t * abx
        proj_y = ay + t * aby

        return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)

    def _find_closest_point(
        self,
        current_pos: Tuple[float, float],
        route: List[Tuple[float, float]],
    ) -> int:
        """找到路线上距离当前点最近的节点索引"""
        min_dist = float("inf")
        closest = 0

        for i, pt in enumerate(route):
            d = haversine_distance(
                current_pos[0], current_pos[1],
                pt[0], pt[1],
            )
            if d < min_dist:
                min_dist = d
                closest = i

        return closest

    def _find_next_turn(
        self,
        route: List[Tuple[float, float]],
        start_idx: int,
        current_pos: Tuple[float, float],
    ) -> Tuple[Optional[int], float, float]:
        """
        从start_idx向前搜索第一个转弯点

        返回:
          (turn_idx, turn_angle, cumulative_distance)
          turn_angle: 转弯角度(正值左转，负值右转)
        """
        cumulative_dist = 0.0

        # 从最近点到当前位置的距离（沿路线）
        if start_idx < len(route) - 1:
            cumulative_dist += haversine_distance(
                route[start_idx][0], route[start_idx][1],
                current_pos[0], current_pos[1],
            )

        prev_bearing = None
        for i in range(start_idx, len(route) - 1):
            bearing = bearing_angle(
                route[i][0], route[i][1],
                route[i + 1][0], route[i + 1][1],
            )

            if prev_bearing is not None:
                diff = ((bearing - prev_bearing + 180) % 360) - 180  # -180~180
                if abs(diff) > 30:  # 转弯阈值
                    return (i + 1, diff, cumulative_dist)

            prev_bearing = bearing

            if i > start_idx:
                cumulative_dist += haversine_distance(
                    route[i][0], route[i][1],
                    route[i + 1][0], route[i + 1][1],
                )

        return (None, 0.0, cumulative_dist)

    def _angle_to_direction(self, angle: float) -> str:
        """将角度差转换为方向描述"""
        if abs(angle) < 30:
            return "straight"
        elif angle > 150 or angle < -150:
            return "uturn"
        elif angle > 0:
            return "left"
        else:
            return "right"

    def _format_distance(self, meters: float) -> str:
        """格式化距离文本"""
        if meters < 10:
            return "前方"
        elif meters < 1000:
            return f"前方{int(meters)}米"
        else:
            return f"前方{ meters / 1000:.1f}公里"

    def _make_instruction(
        self,
        text: str,
        distance: float,
        direction: str,
        should_voice: bool,
    ) -> dict:
        return {
            "instruction": text,
            "distance": distance,
            "direction": direction,
            "should_voice": should_voice,
        }
