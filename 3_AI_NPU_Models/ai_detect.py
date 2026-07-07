"""
AI 后方车辆检测 — YOLOv5/v8 NPU 推理 (RKNNLite API)
v5.0: 使用 rknn-toolkit-lite2 直接推理, 替代 subprocess 调用 C 二进制

检测 → 跟踪 → 计算接近速度 → 分级预警

前提:
  pip3 install rknn_toolkit_lite2-2.3.2-cp3*-linux_aarch64.whl
  .rknn 模型文件放在 /data/yolo/model/

API 兼容 v4.x: detect_vehicles() / assess_danger() 接口不变
"""
import os, time, threading
import numpy as np
from collections import deque
from typing import List, Optional, Tuple, Dict

# ---- RKNNLite 延迟导入 (PC 开发时可 mock) ----
_RKNN_AVAILABLE = False
try:
    from rknnlite.api import RKNNLite
    _RKNN_AVAILABLE = True
except ImportError:
    print("[ai_detect] rknnlite 未安装, 使用 MOCK 模式 (PC调试)")

# ---- 配置 ----
YOLO_MODEL_DIR = "/data/yolo/model"
YOLO_MODEL_FILE = os.path.join(YOLO_MODEL_DIR, "yolov5s-640-640.rknn")
YOLO_ALT_MODEL = os.path.join(YOLO_MODEL_DIR, "yolov8n-640-640.rknn")

IMG_W, IMG_H = 640, 640                      # 模型输入分辨率
CONF_THRESHOLD = 0.5                          # 置信度阈值
NMS_THRESHOLD = 0.45                          # NMS IoU 阈值

# COCO 80 类 → 车辆相关
VEHICLE_CLASSES = {
    1: "bicycle", 2: "car", 3: "motorcycle",
    5: "bus", 7: "truck",
}

# 预警等级 (与 v4.x 兼容)
ALERT_NONE = 0
ALERT_WATCH = 1
ALERT_WARNING = 2
ALERT_DANGER = 3

# ---- YOLOv5 后处理 (基于官方 rknn-toolkit2/examples/onnx/yolov5/test.py) ----
# YOLOv5s 输出: 3个独立 tensor (三尺度)
#   output[0]: [1, 255, 80, 80]  stride=8  小目标
#   output[1]: [1, 255, 40, 40]  stride=16 中目标
#   output[2]: [1, 255, 20, 20]  stride=32 大目标
#   每个 255 = 3 × (5 + 80) = 3 anchors × (xywh+obj+80类)

# YOLOv5s 锚框 (COCO, 640×640)
YOLOV5_ANCHORS = [[10, 13], [16, 30], [33, 23],
                  [30, 61], [62, 45], [59, 119],
                  [116, 90], [156, 198], [373, 326]]
YOLOV5_MASKS = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]  # 每个尺度的 anchor 索引
YOLOV5_STRIDES = [8, 16, 32]

OBJ_THRESH = 0.25   # 目标置信度阈值 (低于最终 CONF_THRESHOLD, 先粗筛)

def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-x))


def _xywh2xyxy(x: np.ndarray) -> np.ndarray:
    y = np.copy(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2  # x1
    y[:, 1] = x[:, 1] - x[:, 3] / 2  # y1
    y[:, 2] = x[:, 0] + x[:, 2] / 2  # x2
    y[:, 3] = x[:, 1] + x[:, 3] / 2  # y2
    return y


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> List[int]:
    """非极大值抑制 (纯 NumPy)"""
    if len(boxes) == 0:
        return []
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while len(order) > 0:
        i = order[0]
        keep.append(i)
        if len(order) == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]
    return keep


def _process_yolov5_scale(input_tensor: np.ndarray, mask: List[int],
                          anchors: List, stride: int):
    """
    处理单个尺度的 YOLOv5 输出

    Args:
      input_tensor: [grid_h, grid_w, 3, 85] 经过 transpose 后的单尺度输出
      mask: 该尺度的 anchor 索引列表
      anchors: 全局 anchor 列表
      stride: 该尺度的 stride

    Returns:
      boxes, classes, scores: 该尺度的检测结果
    """
    grid_h, grid_w = input_tensor.shape[0:2]
    scale_anchors = [anchors[i] for i in mask]

    # box_xy: [grid_h, grid_w, 3, 2]
    box_xy = _sigmoid(input_tensor[..., :2]) * 2 - 0.5
    # box_wh: [grid_h, grid_w, 3, 2]
    box_wh = np.power(_sigmoid(input_tensor[..., 2:4]) * 2, 2) * scale_anchors

    box_confidence = _sigmoid(input_tensor[..., 4:5])  # [grid_h, grid_w, 3, 1]
    box_class_probs = _sigmoid(input_tensor[..., 5:])  # [grid_h, grid_w, 3, 80]

    # 构建网格坐标
    col = np.tile(np.arange(grid_w), grid_h).reshape(grid_h, grid_w)
    row = np.tile(np.arange(grid_h).reshape(-1, 1), (1, grid_w))
    col = col.reshape(grid_h, grid_w, 1, 1).repeat(3, axis=2)
    row = row.reshape(grid_h, grid_w, 1, 1).repeat(3, axis=2)
    grid = np.concatenate((col, row), axis=3)  # [grid_h, grid_w, 3, 2]

    # 解码 box
    box_xy = (box_xy + grid) * stride
    box_wh = box_wh * stride
    box = np.concatenate((box_xy, box_wh), axis=3)  # [grid_h, grid_w, 3, 4]

    # 展平
    box = box.reshape(-1, 4)
    box = _xywh2xyxy(box)
    box_confidence = box_confidence.reshape(-1)
    box_class_probs = box_class_probs.reshape(-1, 80)

    # 过滤
    pos = np.where(box_confidence >= OBJ_THRESH)[0]
    if len(pos) == 0:
        return np.empty((0, 4)), np.empty(0, dtype=int), np.empty(0)

    box = box[pos]
    box_confidence = box_confidence[pos]
    box_class_probs = box_class_probs[pos]

    class_max_score = np.max(box_class_probs, axis=1)
    classes = np.argmax(box_class_probs, axis=1)
    pos2 = np.where(class_max_score >= OBJ_THRESH)[0]

    if len(pos2) == 0:
        return np.empty((0, 4)), np.empty(0, dtype=int), np.empty(0)

    box = box[pos2]
    classes = classes[pos2]
    scores = class_max_score[pos2] * box_confidence[pos2]

    return box, classes, scores


def _parse_yolov5_output(outputs: list) -> List[dict]:
    """
    解析 YOLOv5 三尺度输出 → 检测结果列表

    输入格式 (ONNX YOLOv5s_relu):
      outputs = [ndarray1, ndarray2, ndarray3]
      output[0]: [1, 255, 80, 80]
      output[1]: [1, 255, 40, 40]
      output[2]: [1, 255, 20, 20]

    返回: [{"cls_id": int, "confidence": float, "x1":..., "y1":..., "x2":..., "y2":...}, ...]
    """
    all_boxes, all_classes, all_scores = [], [], []

    for i, (output, mask) in enumerate(zip(outputs, YOLOV5_MASKS)):
        # [1, 255, H, W] → [3, 85, H, W] → [H, W, 3, 85]
        out = output.reshape([3, 85] + list(output.shape[-2:]))
        out = np.transpose(out, (2, 3, 0, 1))  # [H, W, 3, 85]

        boxes, classes, scores = _process_yolov5_scale(
            out, mask, YOLOV5_ANCHORS, YOLOV5_STRIDES[i]
        )

        if len(boxes) > 0:
            all_boxes.append(boxes)
            all_classes.append(classes)
            all_scores.append(scores)

    if not all_boxes:
        return []

    all_boxes = np.concatenate(all_boxes)
    all_classes = np.concatenate(all_classes)
    all_scores = np.concatenate(all_scores)

    # NMS (按类别)
    results = []
    for cls_id in np.unique(all_classes):
        inds = np.where(all_classes == cls_id)[0]
        cls_boxes = all_boxes[inds]
        cls_scores = all_scores[inds]

        keep = _nms(cls_boxes, cls_scores, NMS_THRESHOLD)
        for idx in keep:
            x1, y1, x2, y2 = cls_boxes[idx]
            if cls_scores[idx] > CONF_THRESHOLD:  # 最终置信度过滤
                results.append({
                    "cls_id": int(cls_id),
                    "confidence": float(cls_scores[idx]),
                    "x1": float(x1), "y1": float(y1),
                    "x2": float(x2), "y2": float(y2),
                })

    return results


def _parse_yolov8_output(output) -> List[dict]:
    """
    解析 YOLOv8 输出 [1, 84, 8400] → 检测结果列表

    YOLOv8 输出是转置后的格式:
      [batch, 4 + num_classes, num_anchors] = [1, 84, 8400]
      前 4 行: [cx, cy, w, h]
      后 80 行: class scores
    """
    if isinstance(output, (list, tuple)):
        output = output[0]

    if len(output.shape) == 3 and output.shape[1] == 84:
        # [1, 84, 8400] → [8400, 84]
        predictions = output[0].T
    else:
        predictions = output
        if len(predictions.shape) == 3:
            predictions = predictions[0]

    boxes_raw = predictions[:, :4]
    class_scores = _sigmoid(predictions[:, 4:])

    max_scores = class_scores.max(axis=1)
    max_cls = class_scores.argmax(axis=1)

    mask = max_scores > CONF_THRESHOLD
    if not mask.any():
        return []

    boxes_raw = boxes_raw[mask]
    max_scores = max_scores[mask]
    max_cls = max_cls[mask]

    boxes_xyxy = _xywh2xyxy(boxes_raw)

    results = []
    for cls_id in np.unique(max_cls):
        cls_mask = max_cls == cls_id
        cls_boxes = boxes_xyxy[cls_mask]
        cls_scores = max_scores[cls_mask]

        for idx in _nms(cls_boxes, cls_scores, NMS_THRESHOLD):
            x1, y1, x2, y2 = cls_boxes[idx]
            results.append({
                "cls_id": int(cls_id),
                "confidence": float(cls_scores[idx]),
                "x1": float(x1), "y1": float(y1),
                "x2": float(x2), "y2": float(y2),
            })

    return results


# ---- Detection 对象 (兼容 v4.x 接口) ----
class Detection:
    """单次检测结果"""
    def __init__(self, cls_id, confidence, x1, y1, x2, y2, img_w=IMG_W, img_h=IMG_H):
        self.cls_id = cls_id
        self.confidence = confidence
        self.center_x = (x1 + x2) / 2 / img_w
        self.center_y = (y1 + y2) / 2 / img_h
        self.box_area = ((x2 - x1) * (y2 - y1)) / (img_w * img_h)
        self.name = VEHICLE_CLASSES.get(cls_id, f"cls_{cls_id}")


# ---- NPU 推理引擎 ----
class YOLOEngine:
    """YOLOv5/v8 NPU 推理引擎 (RKNNLite)"""

    def __init__(self, model_path: str = ""):
        self._model_path = model_path or self._auto_detect_model()
        self._rknn: Optional[RKNNLite] = None
        self._initialized = False
        self._lock = threading.Lock()
        self._inference_count = 0
        self._total_time = 0.0
        self._model_type = "yolov5"  # "yolov5" or "yolov8"
        self._parse_fn = _parse_yolov5_output

    def _auto_detect_model(self) -> str:
        """自动检测可用模型: YOLOv5 > YOLOv8"""
        if os.path.exists(YOLO_MODEL_FILE):
            print(f"[YOLO] 使用 YOLOv5s 模型")
            return YOLO_MODEL_FILE
        if os.path.exists(YOLO_ALT_MODEL):
            print(f"[YOLO] 使用 YOLOv8n 模型")
            return YOLO_ALT_MODEL
        return YOLO_MODEL_FILE  # 默认路径, 初始化时会报错提示

    @property
    def is_ready(self) -> bool:
        return self._initialized

    def init(self) -> bool:
        """初始化 NPU 推理引擎 (加载模型 + 初始化 runtime)"""
        if self._initialized:
            return True

        if not _RKNN_AVAILABLE:
            print("[YOLO] RKNNLite 不可用, 运行在 MOCK 模式")
            return False

        if not os.path.exists(self._model_path):
            print(f"[YOLO] 模型文件不存在: {self._model_path}")
            print(f"  请将 .rknn 模型放入 {YOLO_MODEL_DIR}/")
            return False

        try:
            print(f"[YOLO] 加载模型: {self._model_path}")

            # 检测模型类型
            if "yolov8" in self._model_path or "yolov8n" in self._model_path:
                self._model_type = "yolov8"
                self._parse_fn = _parse_yolov8_output
            else:
                self._model_type = "yolov5"
                self._parse_fn = _parse_yolov5_output

            self._rknn = RKNNLite()

            # 加载 RKNN 模型
            ret = self._rknn.load_rknn(self._model_path)
            if ret != 0:
                print(f"[YOLO] load_rknn 失败: {ret}")
                return False

            # 初始化 runtime (RK3568 无需 core_mask)
            ret = self._rknn.init_runtime()
            if ret != 0:
                print(f"[YOLO] init_runtime 失败: {ret}")
                return False

            self._initialized = True
            print(f"[YOLO] NPU 引擎初始化成功 ({self._model_type})")

            # 预热推理 (首次推理通常较慢)
            dummy = np.random.randint(0, 256, (IMG_H, IMG_W, 3), dtype=np.uint8)
            self.infer(dummy)
            print(f"[YOLO] 预热完成")

            return True

        except Exception as e:
            print(f"[YOLO] 初始化失败: {e}")
            self._initialized = False
            return False

    def infer(self, image: np.ndarray) -> List[dict]:
        """
        执行推理

        Args:
          image: numpy ndarray (H, W, 3) RGB uint8

        Returns:
          [{"cls_id": int, "confidence": float, "x1": ..., "y1": ...}, ...]
        """
        if not self._initialized or self._rknn is None:
            return []

        with self._lock:
            t0 = time.perf_counter()

            # 预处理: resize + normalize
            if image.shape[:2] != (IMG_H, IMG_W):
                import cv2
                image = cv2.resize(image, (IMG_W, IMG_H))

            # YOLOv5 输入: [1, 640, 640, 3] float32, 值域 [0, 1]
            if image.dtype == np.uint8:
                input_tensor = image.astype(np.float32) / 255.0
            else:
                input_tensor = image.astype(np.float32)

            input_tensor = np.expand_dims(input_tensor, axis=0)  # [1, H, W, 3]

            # NPU 推理
            outputs = self._rknn.inference(inputs=[input_tensor], data_format=['nhwc'])

            # 后处理: 根据模型类型选择解析器
            if outputs and len(outputs) > 0:
                if self._model_type == "yolov5" and len(outputs) >= 3:
                    # 三尺度输出: [1,255,80,80], [1,255,40,40], [1,255,20,20]
                    detections = _parse_yolov5_output(outputs)
                elif self._model_type == "yolov8" and len(outputs) == 1:
                    # YOLOv8 单输出: [1, 84, 8400]
                    detections = _parse_yolov8_output(outputs[0])
                else:
                    # 尝试自动检测: 3个输出 → YOLOv5, 1个输出 → 尝试 YOLOv8
                    if len(outputs) >= 3:
                        detections = _parse_yolov5_output(outputs)
                    else:
                        detections = _parse_yolov8_output(outputs[0])
            else:
                detections = []

            elapsed = time.perf_counter() - t0
            self._inference_count += 1
            self._total_time += elapsed

            if self._inference_count % 100 == 0:
                avg_ms = (self._total_time / self._inference_count) * 1000
                print(f"[YOLO] 推理: {len(detections)}对象, "
                      f"平均 {avg_ms:.0f}ms ({1000/avg_ms:.0f} FPS)")

            return detections

    def release(self):
        """释放 NPU 资源"""
        if self._rknn:
            try:
                self._rknn.release()
            except Exception:
                pass
            self._rknn = None
            self._initialized = False

    def get_stats(self) -> dict:
        """获取推理统计"""
        avg_ms = (self._total_time / max(self._inference_count, 1)) * 1000
        return {
            "model_type": self._model_type,
            "inference_count": self._inference_count,
            "avg_ms": round(avg_ms, 1),
            "fps": round(1000 / avg_ms, 1) if avg_ms > 0 else 0,
        }


# ---- 全局引擎实例 ----
_engine: Optional[YOLOEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> Optional[YOLOEngine]:
    """获取或创建 YOLO 引擎 (线程安全)"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = YOLOEngine()
                _engine.init()
    return _engine


# ============================================================
# 兼容 v4.x 的公开接口 (ai_alert.py 调用)
# ============================================================

def detect_vehicles(image_path: str) -> list:
    """
    对一张图片做车辆检测 (兼容 v4.x 接口)
    返回: [Detection, ...]
    """
    engine = get_engine()
    if engine is None or not engine.is_ready:
        return []

    try:
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return []
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        raw_detections = engine.infer(img_rgb)

        # 转为 Detection 对象 (兼容旧接口)
        dets = []
        for d in raw_detections:
            cls_id = d["cls_id"]
            if cls_id in VEHICLE_CLASSES and d["confidence"] > 0.5:
                dets.append(Detection(
                    cls_id, d["confidence"],
                    d["x1"], d["y1"], d["x2"], d["y2"],
                    IMG_W, IMG_H,
                ))
        return dets
    except Exception as e:
        print(f"[YOLO] detect_vehicles 异常: {e}")
        return []


def detect_from_frame(frame: np.ndarray) -> List[dict]:
    """
    直接从内存帧做检测 (效率更高, 推荐新代码使用)
    返回: [{"cls_id": int, "confidence": float, "x1":..., "y1":..., "x2":..., "y2":...}, ...]
    """
    engine = get_engine()
    if engine is None or not engine.is_ready:
        return []
    return engine.infer(frame)


# ---- 危险评估 (与 v4.x 完全兼容) ----
_history = deque(maxlen=5)
_alert_level = ALERT_NONE
_last_alert_msg = ""


def _calc_approach_speed(history: list) -> float:
    """从多帧检测框变化估算接近速度 (正值=接近)"""
    if len(history) < 2:
        return 0
    prev = sum(d.box_area for d in history[0]) / max(len(history[0]), 1)
    curr = sum(d.box_area for d in history[-1]) / max(len(history[-1]), 1)
    if prev < 0.001:
        return 0
    return (curr - prev) / prev


def assess_danger(detections: list) -> dict:
    """评估危险等级 (与 v4.x 完全兼容)"""
    global _history, _alert_level, _last_alert_msg
    _history.append(detections)

    if not detections:
        _alert_level = max(ALERT_NONE, _alert_level - 1)
        return {"level": _alert_level, "msg": "", "vehicles": 0}

    vehicles = [d for d in detections if d.cls_id in (2, 5, 7, 3)]
    n_vehicles = len(vehicles)

    if n_vehicles == 0:
        _alert_level = ALERT_NONE
        return {"level": ALERT_NONE, "msg": "", "vehicles": 0}

    biggest = max(vehicles, key=lambda d: d.box_area)
    speed = _calc_approach_speed(_history)

    level = ALERT_WATCH
    msg = f"{n_vehicles}辆车"

    if speed > 0.03:
        level = ALERT_DANGER
        msg = f"⚠ 车辆快速接近! {biggest.name}"
    elif speed > 0.01:
        level = ALERT_WARNING
        msg = f"后方{n_vehicles}辆车接近中"
    elif biggest.box_area > 0.3:
        level = ALERT_WARNING
        msg = "车辆距离较近"

    _alert_level = level
    _last_alert_msg = msg
    return {"level": level, "msg": msg, "vehicles": n_vehicles,
            "speed_index": round(speed, 4), "biggest": biggest.name}


# ---- 检测循环 (在独立线程运行) ----
def run_detection_loop(callback=None, interval: float = 0.2):
    """
    持续检测循环 (兼容 v4.x 接口)

    每 0.2s (5Hz) 从摄像头抓帧 → NPU 推理 → 危险评估 → 回调
    """
    from camera_capture import capture_frame

    engine = get_engine()
    if not engine or not engine.is_ready:
        print("[YOLO] 引擎未就绪, 检测循环无法启动")
        return

    print(f"[YOLO] AI 检测已启动 (5Hz, {engine._model_type})")

    while True:
        try:
            # 抓帧
            path = capture_frame()
            if not path:
                time.sleep(interval)
                continue

            # 检测
            detections = detect_vehicles(path)

            # 评估
            alert = assess_danger(detections)

            if alert["level"] >= ALERT_WARNING:
                print(f"  [{alert['level']}] {alert['msg']}")

            if callback and alert["level"] >= ALERT_WARNING:
                callback(alert)

        except Exception as e:
            print(f"[YOLO] 检测异常: {e}")

        time.sleep(interval)


# ============================================================
# 独立测试
# ============================================================
if __name__ == "__main__":
    import sys

    print("YOLOv5 AI 检测模块 — 测试")
    print(f"  RKNNLite: {'可用' if _RKNN_AVAILABLE else 'MOCK模式'}")
    print(f"  模型路径: {YOLO_MODEL_FILE}")

    engine = get_engine()

    if engine and engine.is_ready:
        print(f"\n引擎状态: 就绪")
        print(f"  模型类型: {engine._model_type}")

        # 测试: 随机图片
        print("\n测试推理 (5次)...")
        import cv2
        for i in range(5):
            # 创建测试图片
            test_img = np.random.randint(0, 256, (IMG_H, IMG_W, 3), dtype=np.uint8)
            t0 = time.perf_counter()
            dets = engine.infer(test_img)
            t = (time.perf_counter() - t0) * 1000
            print(f"  [{i+1}] {len(dets)} 个目标, {t:.0f}ms")

        stats = engine.get_stats()
        print(f"\n统计: {stats}")

        engine.release()
    else:
        print("\n引擎未就绪 (PC MOCK 模式)")
        print("部署到 RK3568 板子上后可正常工作")
        print(f"需要:")
        print(f"  1. pip3 install rknn_toolkit_lite2-2.3.2-*.whl")
        print(f"  2. 将 .rknn 模型放入 {YOLO_MODEL_DIR}/")
