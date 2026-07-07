"""
路线安全分析 — 根据道路名称 & 类型判断安全等级
🟢 安全  🟡 注意  🔴 危险
"""
# 关键词 → 安全等级
DANGER_WORDS = ["快速路", "高速", "高架", "环路", "国道", "省道", "隧道"]
CAUTION_WORDS = ["主干路", "大街", "大道", "路口", "交叉", "辅路", "匝道"]
SAFE_WORDS = ["自行车", "绿道", "骑行", "公园路", "河滨", "小巷", "胡同", "非机动车"]

def road_safety_level(road_name: str, instruction: str = "") -> str:
    """
    返回: "safe" | "caution" | "danger"
    """
    text = road_name + instruction
    for w in DANGER_WORDS:
        if w in text:
            return "danger"
    for w in CAUTION_WORDS:
        if w in text:
            return "caution"
    for w in SAFE_WORDS:
        if w in text:
            return "safe"
    return "caution"  # 未知道路默认黄色

def level_emoji(level: str) -> str:
    return {"safe": "🟢", "caution": "🟡", "danger": "🔴"}.get(level, "🟡")

def level_text(level: str) -> str:
    return {"safe": "安全", "caution": "注意", "danger": "危险"}.get(level, "注意")

def analyze_route(route_steps: list) -> dict:
    """
    分析路线上每段的安全等级
    route_steps: [{"road": "...", "instruction": "...", "distance": 200}, ...]
    返回: {
        "current_level": "caution",
        "current_road": "长安街 (⚠️主干道)",
        "upcoming": [{"road": "南池子大街", "level": "safe", "distance": 300}, ...],
        "summary": "🟡当前注意 | 🟢前方安全"
    }
    """
    if not route_steps:
        return {"current_level": "caution", "current_road": "未知道路",
                "upcoming": [], "summary": "🟡 注意行驶"}

    current = route_steps[0]
    cur_road = current.get("road", "未知道路")
    cur_level = road_safety_level(cur_road, current.get("instruction", ""))

    upcoming = []
    for step in route_steps[1:4]:  # 前3段
        rn = step.get("road", "")
        if rn and rn != cur_road:
            lv = road_safety_level(rn, step.get("instruction", ""))
            upcoming.append({
                "road": rn,
                "level": lv,
                "emoji": level_emoji(lv),
                "distance": step.get("distance", 0),
            })

    # 生成摘要
    parts = [f"{level_emoji(cur_level)}当前{level_text(cur_level)}"]
    if upcoming:
        next_up = upcoming[0]
        parts.append(f"{next_up['emoji']}前方{next_up['distance']}m进入{next_up['road']}")
    summary = " | ".join(parts)

    return {
        "current_level": cur_level,
        "current_road": f"{cur_road}",
        "current_emoji": level_emoji(cur_level),
        "upcoming": upcoming,
        "summary": summary,
        "all_green": all(u["level"] == "safe" for u in upcoming) and cur_level == "safe",
    }
