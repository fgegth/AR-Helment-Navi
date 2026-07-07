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
        self._voice_alerted: bool = False  # 当前路口是否已播报
        self._last_voice_step_idx: int = -1  # 上次播报的步骤索引(防止重复播报)

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
        steps: List[dict],
        heading: float,
    ) -> dict:
        """
        Turn-by-Turn 导航指令 (使用高德API返回的步骤数据)

        算法:
          1. 遍历 steps，找当前位置所在的步骤
          2. 读当前步骤的 action/road/distance
          3. 计算到下一步的距离
          4. 返回指令

        返回:
          {
            "instruction": "前方200米右转进入天目山路",
            "distance": 200.0,
            "direction": "right",
            "should_voice": True,
          }
        """
        if not steps or not current_pos:
            return self._make_instruction("等待路线...", 0, "straight", False)

        # 找当前在哪一步
        cur_step_idx = self._find_current_step(current_pos, steps, route)

        if cur_step_idx >= len(steps) - 1:
            return self._make_instruction("即将到达目的地", 0, "arrived", False)

        cur_step = steps[cur_step_idx]

        # 当前步已经走了多少
        step_dist = self._distance_along_step(current_pos, cur_step, cur_step_idx)

        # 当前步剩余距离 = 步长 - 已走距离
        remaining = max(0, cur_step["distance"] - step_dist)

        # 如果是第一步且刚开始，直接输出当前步的指令
        action = cur_step["action"]
        road = cur_step.get("road", "")
        raw_instruction = cur_step.get("instruction", "")

        # 构造自然语言指令
        if action == "straight":
            text = f"沿{road}直行" if road else "沿当前道路直行"
        elif action in ("left", "right"):
            dir_cn = {"left": "左转", "right": "右转"}
            text = f"{dir_cn[action]}进入{road}" if road else f"{dir_cn[action]}"
            if remaining <= 300:
                text = f"前方{int(remaining)}米{text}" if remaining > 10 else text
        elif action in ("fork_left", "fork_right"):
            text = f"右前方进入匝道" if "right" in action else f"左前方进入匝道"
            if road:
                text += f"({road})"
        elif action == "uturn":
            text = "掉头"
        elif action == "arrived":
            return self._make_instruction("已到达目的地", 0, "arrived", False)
        else:
            text = (raw_instruction or f"沿{road}直行") if road else "沿当前道路直行"

        # 是否需要语音播报 (每一步只播报一次)
        should_voice = (
            0 < remaining < VOICE_ALERT_DISTANCE_M
            and self._last_voice_step_idx != cur_step_idx
        )
        if should_voice:
            self._last_voice_step_idx = cur_step_idx

        return self._make_instruction(text, remaining, action, should_voice)

    def reset_voice_alert(self):
        """重置语音播报标志（路口通过/路线更新后调用）"""
        self._last_voice_step_idx = -1

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
    # 偏航检测 (保留, is_off_route 需要)
    # ================================================================

    def _point_to_polyline_distance(
        self, point: Tuple[float, float], polyline: List[Tuple[float, float]]
    ) -> float:
        """点到路线最短距离(米)"""
        min_dist = float("inf")
        for i in range(len(polyline) - 1):
            a, b = polyline[i], polyline[i + 1]
            seg_len = haversine_distance(a[0], a[1], b[0], b[1])
            if seg_len < 1e-6:
                d = haversine_distance(point[0], point[1], a[0], a[1])
            else:
                lat0, lon0 = a
                def to_m(p):
                    return ((p[0] - lat0) * 111320.0,
                            (p[1] - lon0) * 111320.0 * math.cos(math.radians(lat0)))
                px, py = to_m(point); ax, ay = to_m(a); bx, by = to_m(b)
                abx, aby = bx - ax, by - ay
                t = max(0.0, min(1.0, ((px-ax)*abx + (py-ay)*aby) / (abx*abx + aby*aby + 1e-9)))
                dx, dy = px - (ax + t*abx), py - (ay + t*aby)
                d = math.sqrt(dx*dx + dy*dy)
            min_dist = min(min_dist, d)
        return min_dist

    def _find_closest_point(
        self, current_pos: Tuple[float, float], route: List[Tuple[float, float]]
    ) -> int:
        min_dist = float("inf"); closest = 0
        for i, pt in enumerate(route):
            d = haversine_distance(current_pos[0], current_pos[1], pt[0], pt[1])
            if d < min_dist: min_dist = d; closest = i
        return closest

    # ================================================================
    # Turn-by-Turn 步骤跟踪
    # ================================================================

    def _find_current_step(
        self,
        current_pos: Tuple[float, float],
        steps: List[dict],
        route: List[Tuple[float, float]],
    ) -> int:
        """找当前位置在哪个步骤中（在整个路线坐标中的最近点→映射到步骤）"""
        if not route:
            return 0

        # 找路线坐标上的最近点索引
        min_dist = float("inf")
        closest_coord_idx = 0
        for i, pt in enumerate(route):
            d = haversine_distance(
                current_pos[0], current_pos[1],
                pt[0], pt[1],
            )
            if d < min_dist:
                min_dist = d
                closest_coord_idx = i

        # 映射到 step
        coord_count = 0
        for i, step in enumerate(steps):
            step_len = len(step.get("coords", []))
            coord_count += step_len
            if closest_coord_idx < coord_count:
                return i

        return len(steps) - 1  # 最后一步

    def _distance_along_step(
        self,
        current_pos: Tuple[float, float],
        step: dict,
        step_idx: int,
    ) -> float:
        """
        估算当前位置在步骤内已走的距离
        改进算法: 找到步骤polyline上的最近点, 累加从起点到该点的距离
        """
        coords = step.get("coords", [])
        if not coords:
            return 0
        if len(coords) == 1:
            return haversine_distance(current_pos[0], current_pos[1], coords[0][0], coords[0][1])

        # 找到最近线段, 累加从起点到该线段的距离
        best_dist = float("inf")
        best_seg_idx = 0
        for i in range(len(coords) - 1):
            # 计算当前点到线段i→i+1的距离
            a, b = coords[i], coords[i + 1]
            # 使用_h_point_to_segment 近似计算
            lat0, lon0 = a
            def to_m(p):
                return ((p[0] - lat0) * 111320.0,
                        (p[1] - lon0) * 111320.0 * math.cos(math.radians(lat0)))
            px, py = to_m(current_pos); ax, ay = to_m(a); bx, by = to_m(b)
            abx, aby = bx - ax, by - ay
            seg_len_sq = abx * abx + aby * aby
            if seg_len_sq < 1e-6:
                d = math.sqrt((px - ax) ** 2 + (py - ay) ** 2)
            else:
                t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / seg_len_sq))
                dx, dy = px - (ax + t * abx), py - (ay + t * aby)
                d = math.sqrt(dx * dx + dy * dy)
            if d < best_dist:
                best_dist = d
                best_seg_idx = i

        # 累加从起点到最近线段的距离
        dist = 0.0
        for i in range(best_seg_idx):
            dist += haversine_distance(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
        # 加上在最近线段内的部分距离 (简化: 点到线段起点的距离)
        dist += haversine_distance(current_pos[0], current_pos[1], coords[best_seg_idx][0], coords[best_seg_idx][1])
        return dist

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
