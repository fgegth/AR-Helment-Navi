"""
AI 后方车辆检测 — YOLOv5 NPU 推理
检测 → 跟踪 → 计算接近速度 → 分级预警

V2.2: 新增 CPU 预处理 (绕过 RGA 硬件bug)
  - PIL 缩放 640x480 → 640x640
  - 预处理耗时 ~5-10ms, NPU推理 ~55ms, 总计 ~65ms
"""
import subprocess, os, time, json, threading
from collections import deque

YOLO_DIR = "/data/yolo"
BINARY = f"{YOLO_DIR}/rknn_yolov5_demo"
MODEL = f"{YOLO_DIR}/model/yolov5s-640-640.rknn"
LABELS = f"{YOLO_DIR}/model/coco_80_labels_list.txt"
LIB_PATH = f"{YOLO_DIR}/lib"
INPUT_SIZE = 640  # 模型输入 640x640 (CPU预处理后的统一尺寸)

# 车辆类别 (COCO 80类中的索引)
VEHICLE_CLASSES = {
    2: "car", 3: "motorcycle", 5: "bus", 7: "truck",
    1: "bicycle", 4: "airplane", 6: "train", 8: "boat"
}

# 预警等级
ALERT_NONE = 0      # 无威胁
ALERT_WATCH = 1     # 有车，观察中
ALERT_WARNING = 2   # 车辆接近中
ALERT_DANGER = 3    # 快速接近，危险!

_history = deque(maxlen=5)  # 最近 5 帧的检测结果
_alert_level = ALERT_NONE
_last_alert_msg = ""

class Detection:
    def __init__(self, cls_id, confidence, x1, y1, x2, y2, img_w=640, img_h=480):
        self.cls_id = cls_id
        self.confidence = confidence
        self.center_x = (x1 + x2) / 2 / img_w  # 归一化 0-1
        self.center_y = (y1 + y2) / 2 / img_h
        self.box_area = ((x2 - x1) * (y2 - y1)) / (img_w * img_h)  # 归一化面积
        self.name = VEHICLE_CLASSES.get(cls_id, f"cls_{cls_id}")

def _parse_output(output: str, img_w: int = INPUT_SIZE, img_h: int = INPUT_SIZE) -> list:
    """解析 YOLOv5 输出 → Detection 列表"""
    detections = []
    for line in output.strip().split("\n"):
        parts = line.strip().split()
        if len(parts) >= 6:
            try:
                cls_id = int(parts[0])
                conf = float(parts[1])
                x1, y1, x2, y2 = float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])
                if cls_id in VEHICLE_CLASSES and conf > 0.5:
                    detections.append(Detection(cls_id, conf, x1, y1, x2, y2, img_w, img_h))
            except ValueError:
                pass
    return detections

def detect_vehicles(image_path: str) -> list:
    """对一张图片做车辆检测 (CPU预处理 → NPU推理)"""
    preproc_path = "/tmp/npu_preproc_%d.jpg" % os.getpid()
    try:
        # Step 1: CPU 预处理 — 缩放至 640x640 (绕过 RGA 硬件bug)
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        img = img.resize((INPUT_SIZE, INPUT_SIZE), Image.LANCZOS)
        img.save(preproc_path, "JPEG", quality=95)

        # Step 2: NPU 推理 (输入已是640x640, 无需RGA缩放)
        result = subprocess.run(
            [BINARY, MODEL, preproc_path],
            cwd=YOLO_DIR,
            env={"LD_LIBRARY_PATH": LIB_PATH},
            capture_output=True, timeout=5
        )
        output = result.stdout.decode("utf-8", errors="ignore")

        # 清理临时文件
        try:
            os.remove(preproc_path)
        except Exception:
            pass

        return _parse_output(output)
    except Exception as e:
        print(f"YOLO error: {e}")
        return []

def _calc_approach_speed(history: list) -> float:
    """从多帧检测框变化估算接近速度 (正值=接近, 负值=远离)"""
    if len(history) < 2:
        return 0
    prev = sum(d.box_area for d in history[0]) / max(len(history[0]), 1)
    curr = sum(d.box_area for d in history[-1]) / max(len(history[-1]), 1)
    if prev < 0.001:
        return 0
    return (curr - prev) / prev  # 面积增长率

def assess_danger(detections: list) -> dict:
    """评估危险等级"""
    global _history, _alert_level, _last_alert_msg
    _history.append(detections)

    if not detections:
        _alert_level = max(ALERT_NONE, _alert_level - 1)
        return {"level": _alert_level, "msg": "", "vehicles": 0}

    vehicles = [d for d in detections if d.cls_id in (2, 5, 7, 3)]  # car/bus/truck/motorcycle
    n_vehicles = len(vehicles)

    if n_vehicles == 0:
        _alert_level = ALERT_NONE
        return {"level": ALERT_NONE, "msg": "", "vehicles": 0}

    # 找最近/最大的车辆
    biggest = max(vehicles, key=lambda d: d.box_area)
    speed = _calc_approach_speed(_history)

    level = ALERT_WATCH
    msg = f"后方{n_vehicles}辆车"

    if speed > 0.03:      # 快速接近
        level = ALERT_DANGER
        msg = f"⚠ 车辆快速接近! {biggest.name}"
    elif speed > 0.01:    # 缓慢接近
        level = ALERT_WARNING
        msg = f"后方{n_vehicles}辆车接近中"
    elif biggest.box_area > 0.3:  # 非常近
        level = ALERT_WARNING
        msg = f"车辆距离较近"

    _alert_level = level
    _last_alert_msg = msg
    return {"level": level, "msg": msg, "vehicles": n_vehicles,
            "speed_index": round(speed, 4), "biggest": biggest.name}

def run_detection_loop(callback=None, interval: float = 0.5):
    """
    持续检测循环 (在独立线程中运行)
    callback(alert_dict): 收到预警时回调
    """
    from camera_capture import capture_frame
    print("AI 检测已启动...")
    while True:
        try:
            path = capture_frame()
            if not path:
                time.sleep(interval)
                continue
            detections = detect_vehicles(path)
            alert = assess_danger(detections)
            if alert["level"] >= ALERT_WARNING:
                print(f"  [{alert['level']}] {alert['msg']}")
            if callback and alert["level"] >= ALERT_WARNING:
                callback(alert)
        except Exception as e:
            print(f"检测异常: {e}")
        time.sleep(interval)
