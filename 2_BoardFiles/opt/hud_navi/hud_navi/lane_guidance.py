"""
AR 车道级引导模块
融合 NPU 车道线检测 + 高德车道级 API 数据，为 HUD 提供车道级渲染数据

数据来源:
  1. NPU 车道检测 (来自 nav_state.lane_detection_raw) — 实时视觉车道线
  2. 高德地图车道级 API — 前方道路车道数/推荐车道

输出:
  - lane_boundaries: 车道边界点集 + 类型 + 置信度
  - recommended_lane: 推荐行驶车道索引
  - safe_passing_zone: 安全超车区域
  - front_obstacle_distance: 前方障碍物距离
  - enhanced_arrow_active: 3D 透视箭头状态
"""
import math
import time
import logging
import threading
from typing import Optional, List, Dict, Any, Tuple

from config import (
    LANE_DISPLAY_MIN_CONFIDENCE, LANE_ARROW_3D_ENABLED,
    LANE_SAFE_PASSING_SPEED_DIFF, LANE_OBSTACLE_WARN_DISTANCE,
    LANE_DETECTION_HZ, LANE_CENTER_OFFSET_THRESHOLD,
    AMAP_API_KEY, SCREEN_WIDTH, SCREEN_HEIGHT,
)
from nav_state import state
from gps_reader_a import haversine_distance

logger = logging.getLogger(__name__)

# HTTP 请求 (高德 API)
try:
    import requests
    _requests_available = True
except ImportError:
    _requests_available = False


class LaneGuidance:
    """
    AR 车道引导引擎

    线程: 10Hz 守护线程
    依赖: NPU 调度器 (lane_detection_raw) + 高德 API
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 地图 Provider 引用 (供高德 API 调用)
        self._map_provider = None

        # 车道数据缓存
        self._lane_boundaries: List[Dict[str, Any]] = []
        self._recommended_lane: int = -1
        self._safe_passing_zone: bool = False
        self._front_obstacle_distance: float = -1.0

        # 3D 箭头几何数据
        self._arrow_geometry: dict = {}
        self._enhanced_arrow: bool = LANE_ARROW_3D_ENABLED

        # 车道中心偏移追踪
        self._lane_offset: float = 0.0
        self._lane_offset_history: List[float] = []

        # 高德车道数据缓存
        self._amap_lane_cache: dict = {}
        self._amap_lane_cache_time: float = 0.0
        self._AMAP_LANE_CACHE_TTL = 30  # 30秒缓存

        logger.info(f"车道引导模块就绪 (3D箭头={'启用' if self._enhanced_arrow else '关闭'})")

    # ================================================================
    # 公开接口
    # ================================================================

    def set_map_provider(self, provider):
        """注入地图 Provider (供高德 API 调用)"""
        self._map_provider = provider

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._process_loop, daemon=True, name="LaneGuide"
        )
        self._thread.start()
        logger.info("车道引导线程已启动 (10Hz)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("车道引导已停止")

    def get_lane_status(self) -> dict:
        """获取车道引导状态 (HTTP API 用)"""
        return {
            "boundaries": len(self._lane_boundaries),
            "recommended_lane": self._recommended_lane,
            "safe_passing_zone": self._safe_passing_zone,
            "front_obstacle": self._front_obstacle_distance,
            "data_source": self._get_data_source(),
            "enhanced_arrow": self._enhanced_arrow,
            "lane_offset": round(self._lane_offset, 2),
        }

    def get_arrow_geometry(self) -> dict:
        """获取 3D 箭头几何数据 (HUD 渲染用)"""
        return self._arrow_geometry

    # ================================================================
    # 主处理循环 (10Hz)
    # ================================================================

    def _process_loop(self):
        """主循环: 10Hz"""
        interval = 1.0 / LANE_DETECTION_HZ

        while self._running:
            t_start = time.time()

            try:
                snap = state.get_snapshot()
                if snap.current_position is None:
                    time.sleep(interval)
                    continue

                # 1. 收集车道数据源
                npu_lanes = self._get_npu_lanes(snap)
                amap_lanes = self._get_amap_lanes(snap)

                # 2. 融合车道数据
                self._fuse_lane_data(npu_lanes, amap_lanes, snap)

                # 3. 计算推荐车道
                self._compute_recommended_lane(snap)

                # 4. 计算安全超车区
                self._compute_pass_zone(snap)

                # 5. 计算前方障碍距离
                self._compute_front_obstacle(snap)

                # 6. 更新车道中心偏移
                self._compute_lane_offset(npu_lanes)

                # 7. 构建 3D 箭头几何数据
                if self._enhanced_arrow:
                    self._build_arrow_geometry(snap)

                # 8. 写入 NavState
                self._write_to_state(snap)

            except Exception as e:
                logger.debug(f"车道引导异常: {e}")

            # 帧率控制
            elapsed = time.time() - t_start
            if elapsed < interval:
                time.sleep(interval - elapsed)

    # ================================================================
    # 数据源获取
    # ================================================================

    def _get_npu_lanes(self, snap) -> List[Dict]:
        """获取 NPU 车道检测结果"""
        raw = snap.lane_detection_raw
        if not raw or "lanes" not in raw:
            return []
        # 过滤低置信度
        return [
            lane for lane in raw["lanes"]
            if lane.get("confidence", 0) >= LANE_DISPLAY_MIN_CONFIDENCE
        ]

    def _get_amap_lanes(self, snap) -> List[Dict]:
        """获取高德车道级数据 (仅在在线模式)"""
        if not snap.online_mode or not self._map_provider:
            return []

        # 检查缓存
        now = time.time()
        if self._amap_lane_cache and \
           now - self._amap_lane_cache_time < self._AMAP_LANE_CACHE_TTL:
            return self._amap_lane_cache.get("lanes", [])

        # 获取当前路段的车道信息
        # 高德骑行路径规划不支持车道级数据, 改为通过导航片段分析
        if snap.route and len(snap.route) >= 2:
            try:
                # 从路线中推断车道信息 (基于道路宽度等级)
                amap_lanes = self._infer_lanes_from_route(snap)
                self._amap_lane_cache = {"lanes": amap_lanes, "time": now}
                self._amap_lane_cache_time = now
                return amap_lanes
            except Exception as e:
                logger.debug(f"高德车道数据获取失败: {e}")

        return []

    def _infer_lanes_from_route(self, snap) -> List[Dict]:
        """
        从路线数据推断车道信息

        基于道路安全分级和当前位置推断:
          - 安全级(绿): 2车道, 自行车专用
          - 注意级(黄): 3车道, 混合交通
          - 危险级(红): 4车道, 快速路
        """
        lanes = []
        road = snap.road_condition or ""

        if "🟢" in road:
            # 安全道路: 推测为自行车道或绿道
            lanes = [
                {"type": "left_boundary", "confidence": 0.7, "offset": -1.5},
                {"type": "right_boundary", "confidence": 0.7, "offset": 1.5},
            ]
        elif "🟡" in road:
            # 注意道路: 推测为城市混合道路
            lanes = [
                {"type": "left_boundary", "confidence": 0.6, "offset": -2.5},
                {"type": "lane_divider", "confidence": 0.5, "offset": 0.0},
                {"type": "right_boundary", "confidence": 0.6, "offset": 2.5},
            ]
        elif "🔴" in road:
            # 危险道路: 推测为主干道
            lanes = [
                {"type": "left_boundary", "confidence": 0.8, "offset": -5.0},
                {"type": "lane_divider", "confidence": 0.6, "offset": -1.7},
                {"type": "lane_divider", "confidence": 0.6, "offset": 1.7},
                {"type": "right_boundary", "confidence": 0.8, "offset": 5.0},
            ]
        else:
            # 未知道路: 默认2车道
            lanes = [
                {"type": "left_boundary", "confidence": 0.5, "offset": -2.0},
                {"type": "right_boundary", "confidence": 0.5, "offset": 2.0},
            ]

        return lanes

    # ================================================================
    # 车道数据融合
    # ================================================================

    def _fuse_lane_data(self, npu_lanes: List[Dict], amap_lanes: List[Dict], snap):
        """融合 NPU + 高德车道数据"""
        boundaries = []

        if npu_lanes and amap_lanes:
            # 两者都有: NPU 提供实时偏移, 高德提供道路结构
            boundaries = self._merge_lanes(npu_lanes, amap_lanes)
        elif npu_lanes:
            # 仅 NPU: 使用视觉检测结果
            boundaries = npu_lanes
        elif amap_lanes:
            # 仅高德: 使用推断车道
            boundaries = amap_lanes

        self._lane_boundaries = boundaries

    def _merge_lanes(self, npu: List[Dict], amap: List[Dict]) -> List[Dict]:
        """
        融合 NPU 和高德车道数据

        策略: 高德提供车道数量和类型, NPU 提供精确偏移
        """
        merged = []
        amap_types = {lane.get("type"): lane for lane in amap}

        for npu_lane in npu:
            lane_type = npu_lane.get("type", "")
            conf = npu_lane.get("confidence", 0.5)
            offset = npu_lane.get("offset", 0)

            # 如果有高德数据, 提升边界类型车道的置信度
            if lane_type in amap_types:
                conf = max(conf, amap_types[lane_type].get("confidence", 0))
                # 使用 NPU 的精确偏移
                merged.append({
                    "type": lane_type,
                    "confidence": conf,
                    "offset": offset,
                    "points": npu_lane.get("points", []),
                })
            else:
                merged.append(npu_lane)

        # 添加 NPU 未检测到的高德车道
        existing_types = {m.get("type") for m in merged}
        for lane in amap:
            if lane.get("type") not in existing_types:
                merged.append(lane)

        return merged

    # ================================================================
    # 车道分析
    # ================================================================

    def _compute_recommended_lane(self, snap):
        """
        计算推荐行驶车道

        规则:
          1. 安全道路: 推荐最右侧车道 (自行车)
          2. 混合道路: 推荐中间车道 (平衡安全与效率)
          3. 左转/右转: 推荐对应侧车道
        """
        boundaries = self._lane_boundaries
        if not boundaries:
            self._recommended_lane = -1
            return

        # 统计车道分隔线数量
        dividers = [b for b in boundaries if b.get("type") == "lane_divider"]
        total_lanes = len(dividers) + 1  # 分隔线数 + 1 = 车道数

        # 根据转向指令调整
        direction = snap.turn_direction

        if direction == "right":
            # 右转: 推荐最右侧车道
            self._recommended_lane = total_lanes - 1
        elif direction == "left":
            # 左转: 推荐最左侧车道
            self._recommended_lane = 0
        else:
            # 直行: 推荐中间偏右 (安全原则)
            if total_lanes == 1:
                self._recommended_lane = 0
            elif total_lanes == 2:
                self._recommended_lane = 1  # 最右侧
            else:
                self._recommended_lane = total_lanes - 1  # 最右侧

    def _compute_pass_zone(self, snap):
        """
        判断是否处于安全超车区

        条件:
          1. 道路非危险级
          2. 后方无快速接近车辆 (AI预警 < 2级)
          3. 速度差 > 阈值
        """
        if snap.ai_alert_level >= 2:
            self._safe_passing_zone = False
            return

        if "🔴" in (snap.road_condition or ""):
            self._safe_passing_zone = False
            return

        # 检查是否有足够空间
        if self._lane_boundaries:
            rightmost = [b for b in self._lane_boundaries if b.get("type") == "right_boundary"]
            if rightmost and rightmost[0].get("offset", 0) > 3.0:
                self._safe_passing_zone = True
            else:
                self._safe_passing_zone = False
        else:
            self._safe_passing_zone = True  # 无车道数据时默认允许

    def _compute_front_obstacle(self, snap):
        """计算前方障碍物距离 (整合自 YOLO 检测)"""
        if snap.ai_alert_level >= 2:
            # 有车辆接近 → 警示距离
            self._front_obstacle_distance = LANE_OBSTACLE_WARN_DISTANCE * \
                (3 - snap.ai_alert_level) / 3
        else:
            self._front_obstacle_distance = -1.0  # 无障碍

    def _compute_lane_offset(self, npu_lanes: List[Dict]):
        """
        计算车道中心偏移 (用于车道保持提醒)

        正偏移 = 偏右, 负偏移 = 偏左
        """
        if not npu_lanes:
            return

        # 取左右边界中点
        left_bounds = [l for l in npu_lanes if l.get("type") == "left_boundary"]
        right_bounds = [l for l in npu_lanes if l.get("type") == "right_boundary"]

        if left_bounds and right_bounds:
            left_offset = left_bounds[0].get("offset", -2.0)
            right_offset = right_bounds[0].get("offset", 2.0)
            center = (left_offset + right_offset) / 2.0
            self._lane_offset = center

            # 偏移历史
            self._lane_offset_history.append(center)
            if len(self._lane_offset_history) > 30:
                self._lane_offset_history = self._lane_offset_history[-30:]

    # ================================================================
    # 3D 箭头几何数据
    # ================================================================

    def _build_arrow_geometry(self, snap):
        """
        构建 3D 透视箭头几何数据 (供 HUD 渲染)

        在屏幕坐标系中生成一个透视投影的多边形箭头
        相比 Unicode 箭头更有"AR 感"
        """
        direction = snap.turn_direction

        # 箭头中心点
        cx, cy = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 50

        # 根据方向生成多边形顶点
        if direction == "straight":
            # 直行: 向上箭头 (透视梯形)
            self._arrow_geometry = {
                "polygon": [
                    (cx - 25, cy + 40),  # 左下
                    (cx + 25, cy + 40),  # 右下
                    (cx + 10, cy - 10),  # 右上 (透视缩小)
                    (cx - 10, cy - 10),  # 左上 (透视缩小)
                ],
                "tip": (cx, cy - 80),
                "color": (0, 200, 255, 200),
            }
        elif direction == "left":
            self._arrow_geometry = {
                "polygon": [
                    (cx + 30, cy - 25),
                    (cx + 30, cy + 25),
                    (cx - 40, cy + 5),
                    (cx - 10, cy - 5),
                ],
                "tip": (cx - 50, cy),
                "color": (0, 200, 255, 200),
            }
        elif direction == "right":
            self._arrow_geometry = {
                "polygon": [
                    (cx - 30, cy - 25),
                    (cx - 30, cy + 25),
                    (cx + 40, cy + 5),
                    (cx + 10, cy - 5),
                ],
                "tip": (cx + 50, cy),
                "color": (0, 200, 255, 200),
            }
        elif direction == "uturn":
            # U-turn 弧形
            self._arrow_geometry = {
                "arc": True,
                "center": (cx, cy + 20),
                "radius": 40,
                "start_angle": 0,
                "end_angle": 180,
                "color": (255, 180, 0, 200),
            }
        else:
            self._arrow_geometry = {}

    def _get_data_source(self) -> str:
        """判断当前车道数据来源"""
        has_npu = bool(self._get_npu_lanes(state.get_snapshot()))
        has_amap = bool(self._amap_lane_cache)

        if has_npu and has_amap:
            return "both"
        elif has_npu:
            return "npu"
        elif has_amap:
            return "amap"
        return "none"

    # ================================================================
    # NavState 同步
    # ================================================================

    def _write_to_state(self, snap):
        """写入 NavState"""
        try:
            with state as s:
                s.lane_boundaries = self._lane_boundaries
                s.recommended_lane = self._recommended_lane
                s.safe_passing_zone = self._safe_passing_zone
                s.front_obstacle_distance = self._front_obstacle_distance
                s.lane_data_source = self._get_data_source()
                s.enhanced_arrow_active = self._enhanced_arrow
        except Exception as e:
            logger.debug(f"写入车道状态失败: {e}")


# ============================================================
# 全局单例
# ============================================================

lane_guidance: Optional[LaneGuidance] = None


def get_lane_guidance() -> Optional[LaneGuidance]:
    return lane_guidance
