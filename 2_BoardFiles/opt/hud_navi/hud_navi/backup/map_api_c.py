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

from config import AMAP_API_KEY, AMAP_MAP_ZOOM

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
    def get_map_image(
        self,
        center: Tuple[float, float],
        width: int,
        height: int,
    ) -> Optional[bytes]:
        """
        获取地图图片
        返回: PNG图片字节数据 或 None
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
        self._session.timeout = 10

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
        """
        高德骑行路径规划
        返回路线坐标列表
        """
        try:
            origin_str = f"{origin[1]},{origin[0]}"  # 经度,纬度
            dest_str = f"{destination[1]},{destination[0]}"

            resp = self._session.get(
                f"{self.BASE_URL}/direction/bicycling",
                params={
                    "key": self._api_key,
                    "origin": origin_str,
                    "destination": dest_str,
                    "extensions": "base",
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

            # 解析路线坐标
            route = data["route"]
            paths = route.get("paths", [])
            if not paths:
                logger.warning("高德API: 无可用路线")
                return None

            # 取第一条路线的所有步骤
            steps = paths[0].get("steps", [])
            coords = []
            for step in steps:
                polyline = step.get("polyline", "")
                # 高德polyline格式: "lng1,lat1;lng2,lat2;..."
                for point in polyline.split(";"):
                    if "," in point:
                        lng_str, lat_str = point.split(",")
                        coords.append((float(lat_str), float(lng_str)))

            if coords:
                logger.info(f"高德路径规划成功: {len(coords)}个坐标点")
                return coords
            else:
                return None

        except requests.RequestException as e:
            logger.error(f"高德API网络异常: {e}")
            self._available = False
            return None
        except (KeyError, ValueError, IndexError) as e:
            logger.error(f"高德API响应解析异常: {e}")
            return None

    def get_map_image(
        self,
        center: Tuple[float, float],
        width: int = 1024,
        height: int = 600,
        zoom: int = None,
    ) -> Optional[bytes]:
        """
        高德静态地图
        返回PNG图片字节
        """
        if zoom is None:
            zoom = AMAP_MAP_ZOOM

        try:
            center_str = f"{center[1]},{center[0]}"
            resp = self._session.get(
                f"{self.BASE_URL}/staticmap",
                params={
                    "key": self._api_key,
                    "location": center_str,
                    "zoom": zoom,
                    "size": f"{width}*{height}",
                    "scale": 1,
                    "markers": "mid,0xFF0000,A:" + center_str,  # 中心红点标记
                },
                timeout=15,
            )

            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type", "")
                if "image" in content_type:
                    return resp.content
                else:
                    logger.error(f"高德静态地图返回非图片: {content_type}")
                    return None
            else:
                logger.error(f"高德静态地图HTTP错误: {resp.status_code}")
                return None

        except requests.RequestException as e:
            logger.error(f"高德静态地图网络异常: {e}")
            return None


# ============================================================
# 网络检测工具
# ============================================================

def check_network(timeout: float = None) -> bool:
    """
    检测网络连通性
    尝试连接高德API服务器, 超时即判定为离线
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
    try:
        sock = socket.create_connection((host, 80), timeout=timeout)
        sock.close()
        return True
    except (OSError, socket.timeout):
        return False


# ============================================================
# 离线实现：OSM 地图 — 只做路径规划, 不渲染地图
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
        """A* 最短路径规划 (离线)"""
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
            return coords
        except self._nx.NetworkXNoPath:
            logger.warning("离线规划: 无可达路径")
            return None
        except Exception as e:
            logger.error(f"离线路径规划失败: {e}")
            return None

    def get_map_image(
        self,
        center: Tuple[float, float],
        width: int = 640,
        height: int = 400,
    ) -> Optional[bytes]:
        """
        离线模式不渲染地图底图
        返回 None → HUD 用纯黑背景 + 方向箭头 + 文字提示
        """
        return None
