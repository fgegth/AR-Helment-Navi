"""
智能功能合集: 常用地点 + 行车记录 + 超速提醒
零依赖，纯 Python stdlib
V3.1: 原子JSON写入 + 数据上限保护 + 防只读文件系统崩溃
"""
import json, os, time, math, tempfile
from datetime import datetime

DATA_DIR = "/opt/hud_navi/data"
PLACES_FILE = DATA_DIR + "/frequent_places.json"
RIDES_DIR = DATA_DIR + "/rides"
LOG_FILE = DATA_DIR + "/ride_log.json"

# 数据上限保护
MAX_LOG_ENTRIES = 1000      # 日志最大条数
MAX_PLACES_ENTRIES = 100    # 常用地点最大条数
MAX_RIDE_FILES = 200        # 单次骑行记录文件最大数

# 延迟创建目录 (防只读文件系统导入崩溃)
_data_dir_ready = False

def _ensure_dirs():
    global _data_dir_ready
    if not _data_dir_ready:
        try:
            os.makedirs(RIDES_DIR, exist_ok=True)
            _data_dir_ready = True
        except OSError:
            pass  # 只读文件系统, 静默跳过

def _atomic_write(filepath: str, data):
    """原子写入: 先写临时文件, 再重命名 (防写入中断导致文件损坏)"""
    _ensure_dirs()
    try:
        dirpath = os.path.dirname(filepath)
        fd, tmp = tempfile.mkstemp(dir=dirpath or None)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, filepath)  # 原子操作
        return True
    except Exception:
        return False

# ==================== 常用地点自动学习 ====================

def record_destination(name: str, lat: float, lon: float):
    """每次导航目的地自动记录 (原子写入 + 条目上限保护)"""
    places = {}
    if os.path.exists(PLACES_FILE):
        try:
            with open(PLACES_FILE) as f:
                places = json.load(f)
        except (json.JSONDecodeError, ValueError):
            places = {}  # 损坏文件, 重新开始

    key = f"{lat:.4f},{lon:.4f}"
    if key not in places:
        places[key] = {"name": name, "lat": lat, "lon": lon, "count": 0, "last_used": ""}
    places[key]["count"] += 1
    places[key]["last_used"] = time.strftime("%Y-%m-%d %H:%M")

    # 条目上限: 保留最常用的前N条
    if len(places) > MAX_PLACES_ENTRIES:
        sorted_items = sorted(places.items(), key=lambda x: -x[1]["count"])
        places = dict(sorted_items[:MAX_PLACES_ENTRIES])

    _atomic_write(PLACES_FILE, places)

def get_frequent_places(min_count: int = 2) -> list:
    """获取常用地点列表 (去过 min_count 次以上的)"""
    if not os.path.exists(PLACES_FILE):
        return []
    with open(PLACES_FILE) as f:
        places = json.load(f)
    result = sorted(
        [p for p in places.values() if p["count"] >= min_count],
        key=lambda x: -x["count"]
    )
    return result

def find_place(name: str) -> dict:
    """按名字查找常用地点 (支持模糊匹配)"""
    places = get_frequent_places(1)
    for p in places:
        if name in p["name"] or p["name"] in name:
            return p
    return {}

def rename_place(old_name: str, new_name: str):
    """重命名常用地点 (原子写入)"""
    if not os.path.exists(PLACES_FILE):
        return
    try:
        with open(PLACES_FILE) as f:
            places = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return
    for key, p in places.items():
        if p["name"] == old_name:
            p["name"] = new_name
    _atomic_write(PLACES_FILE, places)

# ==================== 行车数据记录 ====================

_ride_start = None
_ride_points = []
_ride_speeds = []

def ride_start():
    """开始记录一条骑行"""
    global _ride_start, _ride_points, _ride_speeds
    _ride_start = time.time()
    _ride_points = []
    _ride_speeds = []

def ride_log_point(lat: float, lon: float, speed: float):
    """记录一个轨迹点 (每秒调用)"""
    if _ride_start is None:
        ride_start()
    _ride_points.append({"lat": lat, "lon": lon, "t": time.time()})
    _ride_speeds.append(speed)

def ride_end(destination: str = "", distance_km: float = 0):
    """结束骑行，保存日志"""
    global _ride_start, _ride_points, _ride_speeds
    if _ride_start is None or len(_ride_points) < 2:
        return None

    duration_min = (time.time() - _ride_start) / 60
    avg_speed = sum(_ride_speeds) / len(_ride_speeds) if _ride_speeds else 0
    max_speed = max(_ride_speeds) if _ride_speeds else 0
    dist = distance_km if distance_km > 0 else _estimate_distance()

    record = {
        "date": time.strftime("%Y-%m-%d"),
        "time": time.strftime("%H:%M"),
        "duration_min": round(duration_min, 1),
        "distance_km": round(dist, 2),
        "avg_speed": round(avg_speed, 1),
        "max_speed": round(max_speed, 1),
        "destination": destination,
        "points": len(_ride_points),
    }

    # 保存单条记录 (原子写入)
    fname = time.strftime("%Y%m%d_%H%M") + ".json"
    _ensure_dirs()
    _atomic_write(RIDES_DIR + "/" + fname, record)

    # 清理旧记录文件 (保留最近200个)
    try:
        ride_files = sorted(
            [f for f in os.listdir(RIDES_DIR) if f.endswith('.json')],
            reverse=True
        )
        for old_file in ride_files[MAX_RIDE_FILES:]:
            try: os.remove(os.path.join(RIDES_DIR, old_file))
            except OSError: pass
    except OSError:
        pass

    # 更新总日志 (原子写入 + 上限保护)
    log = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                log = json.load(f)
        except (json.JSONDecodeError, ValueError):
            log = []
    log.append(record)
    # 保留最近N条
    if len(log) > MAX_LOG_ENTRIES:
        log = log[-MAX_LOG_ENTRIES:]
    _atomic_write(LOG_FILE, log)

    # 重置
    _ride_start = None
    _ride_points = []
    _ride_speeds = []
    return record

def _estimate_distance() -> float:
    """从轨迹点估算距离 (Haversine)"""
    dist = 0
    for i in range(1, len(_ride_points)):
        lat1, lon1 = _ride_points[i-1]["lat"], _ride_points[i-1]["lon"]
        lat2, lon2 = _ride_points[i]["lat"], _ride_points[i]["lon"]
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        dist += 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return dist

def get_weekly_stats() -> dict:
    """本周骑行统计"""
    if not os.path.exists(LOG_FILE):
        return {"total_rides": 0, "total_km": 0, "total_min": 0}
    with open(LOG_FILE) as f:
        log = json.load(f)
    total_km = sum(r["distance_km"] for r in log)
    total_min = sum(r["duration_min"] for r in log)
    return {
        "total_rides": len(log),
        "total_km": round(total_km, 1),
        "total_min": round(total_min, 0),
        "last_ride": log[-1] if log else None,
    }

# ==================== 超速提醒 ====================

# 默认道路限速 (km/h)，高德 API 可返回更精确值
DEFAULT_SPEED_LIMITS = {
    "safe": 25,     # 自行车道/绿道
    "caution": 20,  # 混合道路
    "danger": 15,   # 主干道/快速路辅路
}

def check_speed(speed_kmh: float, road_level: str = "caution") -> dict:
    """
    检查是否超速
    返回: {"over": bool, "limit": int, "current": float, "warning": str}
    """
    limit = DEFAULT_SPEED_LIMITS.get(road_level, 20)
    over = speed_kmh > limit
    warning = f"⚠ 超速! 限速{limit}km/h，当前{speed_kmh:.0f}km/h" if over else ""
    return {"over": over, "limit": limit, "current": speed_kmh, "warning": warning}
