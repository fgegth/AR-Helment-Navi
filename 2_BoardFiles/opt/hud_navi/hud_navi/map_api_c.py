"""
高德地图 API 封装 — 在线模式
负责人: C

功能:
  1. 静态地图图片获取 (用于HUD底图)
  2. 骑行路径规划
  3. 提供抽象接口 NavigationProvider，便于离线模式替换
"""
import logging
import os
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional

import requests

from config import AMAP_API_KEY

logger = logging.getLogger(__name__)


# ============================================================
# 抽象接口：导航服务提供者
# ============================================================
class NavigationProvider(ABC):
    """导航服务抽象基类，在线和离线模式均实现此接口"""

    @abstractmethod
    def get_route(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> Optional[List[Tuple[float, float]]]:
        """
        路径规划
        返回: 路线坐标列表 [(lat, lon), ...] 或 None(规划失败)
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """检查服务是否可用"""
        ...


# ============================================================
# 在线实现：高德地图
# ============================================================
class AmapProvider(NavigationProvider):
    """
    高德地图 API 实现

    API文档:
      静态地图: https://lbs.amap.com/api/webservice/guide/api/staticmaps
      骑行路径: https://lbs.amap.com/api/webservice/guide/api/direction
    """

    BASE_URL = "https://restapi.amap.com/v3"

    def __init__(self, api_key: str = None):
        self._api_key = api_key or AMAP_API_KEY
        self._available = True
        self._session = requests.Session()
        self._default_timeout = 10  # 兼容 requests.Session 的正确用法

    def is_available(self) -> bool:
        """检查高德API是否可用（网络连通 + Key有效）"""
        try:
            resp = self._session.get(
                f"{self.BASE_URL}/config/district",
                params={"key": self._api_key, "keywords": "北京", "subdistrict": 0},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("status") == "1"
        except Exception:
            pass
        return False

    def get_route(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> Optional[List[Tuple[float, float]]]:
        """[兼容旧接口] 返回路线坐标列表"""
        result = self.get_route_with_steps(origin, destination)
        return result["coords"] if result else None

    def get_route_with_steps(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> Optional[dict]:
        """
        高德骑行路径规划 + Turn-by-Turn步骤提取
        返回: {
            "coords": [(lat, lon), ...],          # 完整路线坐标
            "steps": [{                            # 逐转向步骤
                "instruction": "右转",
                "road": "天目山路",
                "action": "right",
                "orientation": "东",
                "distance": 200.0,                 # 步长(米)
                "coords": [(lat,lon), ...],        # 步内坐标
            }, ...],
            "total_distance": 3200.0,              # 全程米
            "duration": 720,                        # 全程秒
        }
        """
        try:
            origin_str = f"{origin[1]},{origin[0]}"
            dest_str = f"{destination[1]},{destination[0]}"

            resp = self._session.get(
                f"{self.BASE_URL}/direction/driving",
                params={
                    "key": self._api_key,
                    "origin": origin_str,
                    "destination": dest_str,
                    "extensions": "base",
                    "strategy": 0,       # 速度优先
                },
                timeout=10,
            )

            if resp.status_code != 200:
                logger.error(f"高德API请求失败: HTTP {resp.status_code}")
                return None

            data = resp.json()
            if data.get("status") != "1":
                logger.error(f"高德API返回错误: {data.get('info')}")
                return None

            route = data["route"]
            paths = route.get("paths", [])
            if not paths:
                logger.warning("高德API: 无可用路线")
                return None

            path = paths[0]
            api_steps = path.get("steps", [])

            # 解析每步的坐标+转向信息
            coords = []
            steps = []
            for step in api_steps:
                polyline = step.get("polyline", "")
                step_coords = []
                for point in polyline.split(";"):
                    if "," in point:
                        lng_str, lat_str = point.split(",")
                        c = (float(lat_str), float(lng_str))
                        coords.append(c)
                        step_coords.append(c)

                # 高德 action 映射
                action_map = {
                    "左转": "left", "右转": "right",
                    "向左前方": "fork_left", "向右前方": "fork_right",
                    "向左后方": "uturn", "向右后方": "uturn",
                    "直行": "straight", "靠左": "left", "靠右": "right",
                    "左转调头": "uturn", "右转调头": "uturn",
                    "进入环岛": "roundabout", "离开环岛": "straight",
                    "到达目的地": "arrived",
                }
                raw_action = step.get("action", "")
                if isinstance(raw_action, list):
                    raw_action = "到达目的地" if not raw_action else str(raw_action[0])
                action = action_map.get(raw_action, "straight")

                steps.append({
                    "instruction": step.get("instruction", ""),
                    "road": step.get("road", ""),
                    "action": action,
                    "orientation": step.get("orientation", ""),
                    "distance": float(step.get("distance", 0)),
                    "coords": step_coords,
                })

            if coords:
                logger.info(
                    f"高德路径规划成功: {len(coords)}坐标, {len(steps)}步骤")
                return {
                    "coords": coords,
                    "steps": steps,
                    "total_distance": float(path.get("distance", 0)),
                    "duration": int(path.get("duration", 0)),
                }
            return None

        except requests.RequestException as e:
            logger.error(f"高德API网络异常: {e}")
            self._available = False
            return None
        except (KeyError, ValueError, IndexError) as e:
            logger.error(f"高德API响应解析异常: {e}")
            return None



# ============================================================
# 网络检测工具
# ============================================================

def check_network(timeout: float = None) -> bool:
    """
    检测网络连通性 (双通道: HTTP 80 + HTTPS 443)
    高德API走HTTPS, 优先检测443端口
    """
    import socket
    if timeout is None:
        from config import NETWORK_CHECK_TIMEOUT
        timeout = NETWORK_CHECK_TIMEOUT
    try:
        from config import NETWORK_CHECK_URL
        host = NETWORK_CHECK_URL
    except ImportError:
        host = "restapi.amap.com"
    # 优先检测HTTPS端口(443), 失败再试HTTP(80)
    for port in (443, 80):
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return True
        except (OSError, socket.timeout):
            continue
    return False


# ============================================================
# 离线实现：OSM 地图 — 只做路径规划
# ============================================================

class OfflineProvider(NavigationProvider):
    """
    离线地图提供者
    使用本地 .osm 文件 + osmnx + networkx 进行 A* 路径规划
    get_map_image() 返回 None → HUD 显示纯黑底 + AR 箭头大字
    """

    def __init__(self, osm_file: str, tile_dir: str):
        self._osm_file = osm_file
        self._tile_dir = tile_dir
        self._graph = None
        self._available = False
        self._map_loaded = False

        try:
            import osmnx as ox
            import networkx as nx
            self._ox = ox
            self._nx = nx
            self._imports_ok = True
        except ImportError:
            logger.warning("osmnx/networkx 未安装，离线模式不可用")
            self._imports_ok = False

    def is_available(self) -> bool:
        return self._imports_ok and os.path.exists(self._osm_file)

    def is_map_loaded(self) -> bool:
        return self._map_loaded

    def load_map(self) -> bool:
        """加载 OSM 地图文件"""
        if not self._imports_ok:
            return False
        if not os.path.exists(self._osm_file):
            logger.error(f"OSM文件不存在: {self._osm_file}")
            return False

        try:
            logger.info(f"加载离线地图: {self._osm_file}")
            self._graph = self._ox.graph_from_xml(self._osm_file)
            self._available = True
            self._map_loaded = True
            logger.info(f"离线地图加载成功: {len(self._graph.nodes)}节点, {len(self._graph.edges)}边")
            return True
        except Exception as e:
            logger.error(f"离线地图加载失败: {e}")
            return False

    def get_route(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> Optional[List[Tuple[float, float]]]:
        """[兼容] A* 最短路径规划"""
        r = self.get_route_with_steps(origin, destination)
        return r["coords"] if r else None

    def get_route_with_steps(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> Optional[dict]:
        """A* 最短路径规划 (离线, 无详细步骤信息)"""
        if self._graph is None:
            if not self.load_map():
                return None

        try:
            orig_node = self._ox.nearest_nodes(self._graph, origin[1], origin[0])
            dest_node = self._ox.nearest_nodes(self._graph, destination[1], destination[0])
            path_nodes = self._nx.astar_path(
                self._graph, orig_node, dest_node, weight="length"
            )
            coords = []
            for node_id in path_nodes:
                node = self._graph.nodes[node_id]
                coords.append((node["y"], node["x"]))
            logger.info(f"离线A*路径规划: {len(coords)}个航点")
            # 离线模式无详细步骤, 生成一个默认"直行"步骤
            return {
                "coords": coords,
                "steps": [{
                    "instruction": "沿路线直行",
                    "road": "",
                    "action": "straight",
                    "orientation": "",
                    "distance": 0,
                    "coords": coords,
                }],
                "total_distance": 0,
                "duration": 0,
            }
        except self._nx.NetworkXNoPath:
            logger.warning("离线规划: 无可达路径")
            return None
        except Exception as e:
            logger.error(f"离线路径规划失败: {e}")
            return None
