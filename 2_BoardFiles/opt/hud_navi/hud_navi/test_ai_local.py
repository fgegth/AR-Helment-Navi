"""
本地 AI 测试 — 模拟 YOLOv5 输出, 验证检测→跟踪→预警全链路
不需要板子, 不需要摄像头
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from ai_detect import Detection, _parse_output, assess_danger
from ai_detect import _history  # 多帧历史

# ═══════════════════════════════════════
# 测试 1: 解析 YOLOv5 输出
# ═══════════════════════════════════════
print("=" * 50)
print("测试 1: 解析 YOLOv5 输出")
print("=" * 50)

# 模拟 SDK demo 的输出格式
mock_output = """
2 0.85 100 200 250 400
5 0.72 300 150 500 380
2 0.63 400 100 550 280
7 0.91 50 300 180 460
1 0.55 600 200 630 250
3 0.44 200 50 280 120
"""
dets = _parse_output(mock_output)
print(f"检测到 {len(dets)} 个车辆:")
for d in dets:
    print(f"  [{d.name}] 置信度:{d.confidence:.2f} 中心:({d.center_x:.2f},{d.center_y:.2f}) 面积:{d.box_area:.3f}")

assert len(dets) == 5, f"应检测 5 个, 实际 {len(dets)}"
assert dets[0].name == "car"
assert dets[3].name == "truck"
print("✅ 解析测试通过")

# ═══════════════════════════════════════
# 测试 2: 多帧跟踪 + 接近速度
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("测试 2: 多帧跟踪 + 接近速度")
print("=" * 50)

# 清空历史
_alert_level = 0

# 第 1 帧: 远处有一辆车
frame1 = [Detection(2, 0.8, 300, 200, 340, 240, 640, 480)]  # 小车
a1 = assess_danger(frame1)
print(f"帧1: {a1['msg']} (level={a1['level']})")

# 第 2 帧: 车变大了 (接近中)
frame2 = [Detection(2, 0.82, 280, 180, 360, 260, 640, 480)]  # 变大了
a2 = assess_danger(frame2)
print(f"帧2: {a2['msg']} (speed={a2['speed_index']})")

# 第 3 帧: 更大更近
frame3 = [Detection(2, 0.85, 260, 150, 380, 290, 640, 480)]  # 更大
a3 = assess_danger(frame3)
print(f"帧3: {a3['msg']} (level={a3['level']}, speed={a3['speed_index']})")

# 第 4 帧: 非常近!
frame4 = [Detection(2, 0.88, 200, 100, 440, 350, 640, 480)]  # 占画面大半
a4 = assess_danger(frame4)
print(f"帧4: {a4['msg']} (level={a4['level']})")

assert a1["level"] == 1  # 观察
assert a4["level"] >= 2, f"第4帧应警告, 实际 {a4['level']}"
print("✅ 跟踪+预警测试通过")

# ═══════════════════════════════════════
# 测试 3: 空场景
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("测试 3: 空场景")
print("=" * 50)
for _ in range(3):
    a = assess_danger([])
print(f"连续空帧后: level={a['level']}")
assert a["level"] == 0
print("✅ 空场景测试通过")

# ═══════════════════════════════════════
# 测试 4: 实际 bus.jpg 解析测试
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("测试 4: 模拟 bus.jpg 检测")
print("=" * 50)
# YOLOv5 在 bus.jpg 上通常检测到 bus + person + car
bus_output = """
5 0.92 50 80 400 350
0 0.87 300 200 350 400
2 0.76 420 150 550 280
"""
bus_dets = _parse_output(bus_output)
print(f"bus.jpg: 检测到 {len(bus_dets)} 个对象")
for d in bus_dets:
    print(f"  [{d.name}] conf={d.confidence:.2f}")
ba = assess_danger(bus_dets)
print(f"预警: level={ba['level']} msg={ba['msg']}")
assert len(bus_dets) == 2, "应检测到 2 辆 (bus + car)"
print("✅ bus.jpg 测试通过")

# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("🎉 全部测试通过!")
print("=" * 50)
print("""
板子插回后真实测试:
  1. adb push yolo demo files
  2. python test_ai.py  (用 bus.jpg)
  3. 插 USB 摄像头
  4. python -c "from ai_alert import start_ai_monitor; start_ai_monitor()"
""")
