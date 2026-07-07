"""
意图引擎 — Whisper自然语言文本 → 结构化导航指令
纯Python标准库, 零外部依赖 (预留 WhiteLightning ONNX 升级口)
"""
import json, os, re, logging
from difflib import SequenceMatcher
from smart_features import get_frequent_places

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════
# 意图 → 动作映射规则
# ═══════════════════════════════════════
INTENT_RULES = [
    # 否定 → 忽略 (最前)
    (r'(不要|别|不想|不用|不需要|不去了).*(导航|去|走)', "ignore", False),
    # 取消 (支持模糊匹配: 取消/取消导航/取消道行/停止导航等)
    (r'.*(取消|停止|关闭|结束).*', "cancel", False),
    (r'^别.*', "cancel", False),
    # 查询
    (r'(还有|离|距离|多远|到哪里了|到哪了|什么位置)', "status", False),
    (r'(查|报|说).*(距离|多远|位置|进度)', "status", False),
    # 继续
    (r'(开始|继续|走|出发|启动).*(导航|走|吧)', "continue", False),
    # 导航意图 — 提取地名(放最后, 最宽匹配)
    (r'(去|到|导航到|前往|我要去|帮我去|给我导航到|导航去|导航至|导航)\s*(.+)', "navigate", True),
    (r'(.+?)(?:请|帮我|给我).*导航', "navigate", True),
    (r'我要(?:去|回|到)(.+)', "navigate", True),
    (r'(回家)', "navigate", True),
]

# 别名映射 (处理口语简称)
PLACE_ALIASES = {
    "公司": "公司", "单位": "公司", "上班": "公司",
    "家": "家", "回家": "家", "家里": "家",
    "安建大": "安徽建筑大学", "建筑大学": "安徽建筑大学",
    "合工大": "合肥工业大学", "工业大学": "合肥工业大学",
    "滨湖": "滨湖会展中心", "会展中心": "滨湖会展中心",
    "安徽建筑大学": "安徽建筑大学", "安建大": "安徽建筑大学", "建筑大学": "安徽建筑大学",
}


def _fuzzy_score(a, b):
    """简单模糊匹配 (0~1)"""
    return SequenceMatcher(None, a, b).ratio()


# 硬编码高频目的地坐标 (永久可用, 不依赖frequent_places)
HARDCODED_PLACES = {
    # 大学
    "安徽建筑大学": (31.745825, 117.227425), "安建大": (31.745825, 117.227425),
    "建筑大学": (31.745825, 117.227425),
    "合肥工业大学": (31.841767, 117.296145), "合工大": (31.841767, 117.296145),
    "中国科学技术大学": (31.8222, 117.2742), "中科大": (31.8222, 117.2742), "科大": (31.8222, 117.2742),
    "安徽大学": (31.7683, 117.1875), "安大": (31.7683, 117.1875),
    # 交通枢纽
    "合肥火车站": (31.8862, 117.3108), "火车站": (31.8862, 117.3108),
    "合肥南站": (31.8017, 117.2869), "南站": (31.8017, 117.2869),
    # 商圈/购物
    "之心城": (31.8620, 117.2550), "国购广场": (31.8620, 117.2550),
    "步行街": (31.8650, 117.2950), "淮河路": (31.8650, 117.2950),
    "天鹅湖万达": (31.8230, 117.2290), "万达广场": (31.8230, 117.2290), "万达": (31.8230, 117.2290),
    # 景点/公园
    "逍遥津": (31.8650, 117.3010), "逍遥津公园": (31.8650, 117.3010),
    "天鹅湖": (31.8180, 117.2280),
    "包公园": (31.8580, 117.2950), "包公祠": (31.8580, 117.2950),
    "大蜀山": (31.8420, 117.1740), "蜀山森林公园": (31.8420, 117.1740),
    # 政务/办公
    "市政府": (31.8250, 117.2300), "政务中心": (31.8250, 117.2300), "天鹅湖市政府": (31.8250, 117.2300),
    "公司": (31.8350, 117.2500), "单位": (31.8350, 117.2500), "上班": (31.8350, 117.2500),
    # 医院
    "省立医院": (31.8620, 117.2850), "安徽省立医院": (31.8620, 117.2850),
    "安医附院": (31.8480, 117.2680), "安医": (31.8480, 117.2680),
    # 地标
    "滨湖会展中心": (31.7100, 117.2800), "滨湖": (31.7100, 117.2800),
    # 无固定坐标（由 frequent_places 解析）
    "回家": None, "家": None,
}

def _resolve_place(target_text: str):
    """
    口语地名 → 坐标。优先级: 硬编码 → 别名查库 → 模糊匹配
    返回: (name, lat, lon) 或 (name, None, None) 表示已匹配地名但无固定坐标
    """
    target = target_text.strip().rstrip("。，！,.!?？")
    if not target:
        return None, None, None

    # 0. 硬编码 (最高优先级, 模糊匹配容忍Whisper误差)
    for key, value in HARDCODED_PLACES.items():
        if key in target or target in key:
            if value is None:  # 无固定坐标（如"家"），返回地名让调用方处理
                return key, None, None
            return key, value[0], value[1]
        # 模糊: 比较每个字符, 匹配率>50%即接受
        common = sum(1 for c in key if c in target)
        if common >= len(key) * 0.5:
            if value is None:
                return key, None, None
            return key, value[0], value[1]

    # 1. 清洗前缀后缀
    for prefix in ["去", "到", "前往", "导航到", "导航去", "导航至"]:
        if target.startswith(prefix) and len(target) > len(prefix):
            target = target[len(prefix):]; break
    for cutoff in ["帮我", "请", "给", "看看", "查"]:
        idx = target.find(cutoff)
        if idx > 0: target = target[:idx].strip(); break

    # 2. 别名查表
    for alias, full in PLACE_ALIASES.items():
        if alias in target or target in alias:
            target = full
            break

    # 2. 查常用地点
    places = get_frequent_places(min_count=1)
    best_name, best_lat, best_lon, best_score = None, None, None, 0

    for p in places:
        name = p.get("name", "")
        # 精确包含
        if target in name or name in target:
            return name, p["lat"], p["lon"]
        # 模糊匹配
        score = _fuzzy_score(target, name)
        if score > best_score:
            best_score = score
            best_name = name
            best_lat = p["lat"]
            best_lon = p["lon"]

    # 3. 阈值判断
    if best_score > 0.45 and best_name:
        return best_name, best_lat, best_lon

    return None, None, None


def extract_intent(text: str) -> dict:
    """
    输入: Whisper 输出的自然语言文本, 如 "我要去公司请给我导航"
    输出: {
        "intent": "navigate",    # navigate/cancel/status/continue/unknown
        "target": "公司",         # 目的地名称
        "lat": 31.7520,          # 目的地坐标
        "lon": 117.2518,
        "confidence": 0.85,      # 置信度
    }
    """
    text = text.strip()
    if not text:
        return {"intent": "unknown", "target": "", "lat": None, "lon": None, "confidence": 0}

    # 匹配意图规则
    for pattern, intent, extract_target in INTENT_RULES:
        m = re.search(pattern, text)
        if m:
            confidence = 0.85
            result = {"intent": intent, "target": "", "lat": None, "lon": None, "confidence": confidence}

            if extract_target:
                target_text = m.group(2) if len(m.groups()) >= 2 else m.group(1)
                name, lat, lon = _resolve_place(target_text)
                if name:
                    result["target"] = name
                    result["lat"] = lat
                    result["lon"] = lon
                    result["confidence"] = 0.90
                else:
                    # 有导航意图但没匹配到地点 → 保守标为 unknown
                    result["intent"] = "unknown"
                    result["target"] = target_text
                    result["confidence"] = 0.40

            return result

    # 无匹配 → 回退: 直接模糊匹配地名 (纯地名也触发导航)
    name, lat, lon = _resolve_place(text)
    if name:
        return {"intent": "navigate", "target": name, "lat": lat, "lon": lon, "confidence": 0.65}
    # 模糊匹配frequent_places兜底
    places = get_frequent_places(min_count=1)
    for p in places:
        pn = p.get("name", "")
        if _fuzzy_score(text, pn) > 0.40:
            return {"intent": "navigate", "target": pn, "lat": p["lat"], "lon": p["lon"], "confidence": 0.50}

    return {"intent": "unknown", "target": text[:30], "lat": None, "lon": None, "confidence": 0.10}
