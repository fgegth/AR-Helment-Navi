"""
骑行数据分析 — 从历史记录中提取洞察
"""
import json, os, math
from collections import defaultdict

LOG_FILE = "/opt/hud_navi/data/ride_log.json"
RIDES_DIR = "/opt/hud_navi/data/rides"

def load_log():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE) as f:
        return json.load(f)

def get_detailed_stats() -> dict:
    """完整分析报告"""
    log = load_log()
    if not log:
        return {"error": "暂无骑行数据"}

    distances = [r["distance_km"] for r in log]
    speeds = [r.get("avg_speed", 0) for r in log if r.get("avg_speed", 0) > 0]
    durations = [r["duration_min"] for r in log]
    destinations = defaultdict(int)
    for r in log:
        d = r.get("destination", "未知")
        if d and d != "未知":
            destinations[d] += 1

    # 按天分组
    by_day = defaultdict(lambda: {"count": 0, "km": 0, "min": 0})
    for r in log:
        day = r.get("date", "unknown")
        by_day[day]["count"] += 1
        by_day[day]["km"] += r["distance_km"]
        by_day[day]["min"] += r["duration_min"]

    # 按周
    by_week = defaultdict(lambda: {"count": 0, "km": 0})
    for r in log:
        week = r.get("date", "")[:7]  # YYYY-MM
        by_week[week]["count"] += 1
        by_week[week]["km"] += r["distance_km"]

    # 趋势 (最近7天 vs 之前7天)
    sorted_days = sorted(by_day.keys())
    recent = sorted_days[-7:] if len(sorted_days) >= 7 else sorted_days
    older = sorted_days[:-7] if len(sorted_days) > 7 else []
    recent_km = sum(by_day[d]["km"] for d in recent)
    older_km = sum(by_day[d]["km"] for d in older) if older else 0
    trend = "↗" if recent_km > older_km * 1.1 else ("↘" if recent_km < older_km * 0.9 else "→")

    # 时段偏好
    hours = defaultdict(int)
    for r in log:
        t = r.get("time", "00:00")
        h = int(t.split(":")[0]) if ":" in t else 0
        hours[h] += 1
    peak_hour = max(hours, key=hours.get) if hours else 0

    return {
        "total": len(log),
        "total_km": round(sum(distances), 1),
        "total_hours": round(sum(durations) / 60, 1),
        "avg_speed": round(sum(speeds) / len(speeds), 1) if speeds else 0,
        "max_speed": round(max(speeds), 1) if speeds else 0,
        "longest_ride": round(max(distances), 1),
        "fastest_ride": {
            "date": log[speeds.index(max(speeds))]["date"] if speeds else "",
            "speed": round(max(speeds), 1) if speeds else 0,
        },
        "weekly_avg_km": round(sum(distances) / max(len(by_week), 1), 1),
        "daily_avg_km": round(sum(distances) / max(len(by_day), 1), 1),
        "trend": trend,
        "peak_hour": peak_hour,
        "top_destinations": sorted(destinations.items(), key=lambda x: -x[1])[:5],
        "by_day": {k: v for k, v in sorted(by_day.items())[-14:]},
        "by_week": {k: v for k, v in sorted(by_week.items())[-8:]},
    }

def get_speed_distribution() -> dict:
    """速度分布"""
    log = load_log()
    buckets = {"0-10": 0, "10-15": 0, "15-20": 0, "20-25": 0, "25+": 0}
    for r in log:
        s = r.get("avg_speed", 0)
        if s < 10: buckets["0-10"] += 1
        elif s < 15: buckets["10-15"] += 1
        elif s < 20: buckets["15-20"] += 1
        elif s < 25: buckets["20-25"] += 1
        else: buckets["25+"] += 1
    return buckets

def get_monthly_trend() -> list:
    """月度趋势"""
    log = load_log()
    months = defaultdict(lambda: {"km": 0, "rides": 0})
    for r in log:
        m = r.get("date", "")[:7]
        months[m]["km"] += r["distance_km"]
        months[m]["rides"] += 1
    return [{"month": k, **v} for k, v in sorted(months.items())[-6:]]
