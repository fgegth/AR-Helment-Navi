"""AI 检测测试 — 用 SDK 自带的 bus.jpg 测试 (不依赖摄像头)"""
import os, sys
os.environ["LD_LIBRARY_PATH"] = "/data/yolo/lib"

BINARY = "/data/yolo/rknn_yolov5_demo"
MODEL = "/data/yolo/model/yolov5s_qsm368zp.rknn"
TEST_IMG = "/data/yolo/model/bus.jpg"

if not os.path.exists(BINARY):
    print("请先推 YOLOv5 demo 到 /data/yolo/")
    print("  adb push rknn_yolov5_demo /data/yolo/")
    print("  adb push yolov5s_qsm368zp.rknn /data/yolo/model/")
    sys.exit(1)

import subprocess
result = subprocess.run([BINARY, MODEL, TEST_IMG],
    capture_output=True, timeout=10)
output = result.stdout.decode("utf-8", errors="ignore")
print("YOLOv5 output:")
print(output[:500] if output else "(empty)")

# 解析
from ai_detect import _parse_output, assess_danger
dets = _parse_output(output)
print(f"\n检测到 {len(dets)} 个车辆:")
for d in dets:
    print(f"  {d.name} 置信度:{d.confidence:.2f} 位置:({d.center_x:.2f},{d.center_y:.2f})")
alert = assess_danger(dets)
print(f"\n预警等级: {alert['level']} — {alert['msg']}")
