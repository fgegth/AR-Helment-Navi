"""
NPU 多模型时分复用调度器 (RKNNLite API)
v5.0: 用 RKNNLite 直接管理多模型, 替代 subprocess 调用 C 二进制

RK3568 NPU: 1 TOPS, 单核 — 模型需分时复用, 不能并行

调度策略: 优先级抢占 + 时间片轮转
  P0 (安全): YOLOv5s 车辆检测    5Hz   (200ms)
  P1 (导航): 车道线/OCR 检测     2Hz   (500ms)
  P2 (信息): 姿态/分类           1Hz   (1000ms)

每个模型保持独立的 RKNNLite 实例, kernel 驱动自动序列化 NPU 访问

与现有模块兼容:
  - ai_detect.py: 使用其 YOLOEngine (共享实例)
  - nav_state: 结果写入 state
  - camera_capture: 共用一个摄像头
"""
import os, time, logging, threading
from collections import deque
from typing import Optional, Dict, Any, List

# ---- RKNNLite ----
_RKNN_AVAILABLE = False
try:
    from rknnlite.api import RKNNLite
    _RKNN_AVAILABLE = True
except ImportError:
    pass

from config import (
    NPU_MODEL_YOLO, NPU_MODEL_LANE, NPU_MODEL_SIGN,
    NPU_YOLO_PRIORITY, NPU_LANE_PRIORITY, NPU_SIGN_PRIORITY,
    NPU_YOLO_INTERVAL, NPU_LANE_INTERVAL, NPU_SIGN_INTERVAL,
    NPU_INFERENCE_TIMEOUT, NPU_FRAME_PATH,
)
from nav_state import state

logger = logging.getLogger(__name__)

# ============================================================
# 模型基类
# ============================================================
class NPUModel:
    """单个 NPU 模型的封装"""

    def __init__(self, key: str, cfg: dict):
        self.key = key
        self.name = cfg.get("name", key)
        self.priority = cfg["priority"]
        self.interval = cfg["interval"]
        self.model_path = cfg["model_path"]
        self._rknn: Optional[RKNNLite] = None
        self._available = False
        self._inference_count = 0
        self._total_time = 0.0
        self._last_run = 0.0

        self._check_available()

    def _check_available(self):
        """检查模型文件是否存在"""
        if os.path.exists(self.model_path) and _RKNN_AVAILABLE:
            self._available = True
        else:
            logger.debug(f"  NPU模型 {self.name}: 不可用 ({self.model_path})")

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def is_loaded(self) -> bool:
        return self._rknn is not None

    def load(self) -> bool:
        """加载 RKNN 模型 + 初始化 runtime"""
        if not self._available:
            return False
        if self._rknn is not None:
            return True

        try:
            self._rknn = RKNNLite()
            ret = self._rknn.load_rknn(self.model_path)
            if ret != 0:
                logger.warning(f"  {self.name} load_rknn 失败: {ret}")
                self._available = False
                return False

            ret = self._rknn.init_runtime()
            if ret != 0:
                logger.warning(f"  {self.name} init_runtime 失败: {ret}")
                self._available = False
                return False

            logger.info(f"  {self.name}: 已加载")
            return True
        except Exception as e:
            logger.error(f"  {self.name} 加载异常: {e}")
            self._available = False
            return False

    def infer(self, input_tensor) -> Optional[list]:
        """执行推理, 返回原始输出列表"""
        if self._rknn is None:
            return None

        t0 = time.perf_counter()
        try:
            outputs = self._rknn.inference(inputs=[input_tensor])
            elapsed = time.perf_counter() - t0
            self._inference_count += 1
            self._total_time += elapsed
            self._last_run = time.time()
            return outputs
        except Exception as e:
            logger.error(f"  {self.name} 推理异常: {e}")
            return None

    def release(self):
        if self._rknn:
            try:
                self._rknn.release()
            except Exception:
                pass
            self._rknn = None

    def get_stats(self) -> dict:
        avg_ms = (self._total_time / max(self._inference_count, 1)) * 1000
        return {
            "name": self.name,
            "available": self._available,
            "loaded": self.is_loaded,
            "inferences": self._inference_count,
            "avg_ms": round(avg_ms, 1),
            "last_run_ago": round(time.time() - self._last_run, 2),
        }


# ============================================================
# 模型注册表
# ============================================================
MODEL_REGISTRY = {
    "yolo": {
        "name": "YOLOv5s 车辆检测",
        "priority": NPU_YOLO_PRIORITY,
        "interval": NPU_YOLO_INTERVAL,
        "model_path": NPU_MODEL_YOLO,
        "vehicle_classes": {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"},
    },
    "lane": {
        "name": "车道/OCR 检测",
        "priority": NPU_LANE_PRIORITY,
        "interval": NPU_LANE_INTERVAL,
        "model_path": NPU_MODEL_LANE,
    },
    "sign": {
        "name": "交通标志分类",
        "priority": NPU_SIGN_PRIORITY,
        "interval": NPU_SIGN_INTERVAL,
        "model_path": NPU_MODEL_SIGN,
    },
}


# ============================================================
# NPU 调度器
# ============================================================
class NPUScheduler:
    """
    NPU 多模型调度器
    在独立守护线程中运行, 按优先级+时间片调度模型推理
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 创建模型实例
        self._models: Dict[str, NPUModel] = {}
        for key, cfg in MODEL_REGISTRY.items():
            model = NPUModel(key, cfg)
            self._models[key] = model

        # 推理结果缓存
        self._yolo_history = deque(maxlen=5)
        self._alert_level: int = 0
        self._alert_msg: str = ""

        # NPU 健康
        self._npu_health: str = "ok"
        self._npu_error_count: int = 0
        self._total_inferences: int = 0
        self._total_errors: int = 0

        self._current_model: str = ""

    # ================================================================
    # 初始化 & 生命周期
    # ================================================================

    def load_all(self):
        """加载所有可用模型"""
        loaded = 0
        for key in ["yolo", "lane", "sign"]:  # 按优先级顺序加载
            if self._models[key].is_available:
                if self._models[key].load():
                    loaded += 1
        logger.info(f"NPU 调度器: {loaded}/{len(self._models)} 个模型已加载")

        if loaded == 0:
            self._npu_health = "error"
            logger.warning("无可用 NPU 模型!")

    def start(self):
        if self._running:
            return

        self.load_all()
        self._running = True
        self._thread = threading.Thread(
            target=self._scheduler_loop, daemon=True, name="NPU-Sched"
        )
        self._thread.start()
        logger.info("NPU 调度器已启动")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)

        # 释放所有模型
        for model in self._models.values():
            model.release()

        logger.info(f"NPU 调度器已停止 (总推理 {self._total_inferences} 次, "
                    f"错误 {self._total_errors} 次)")

    def get_status(self) -> dict:
        return {
            "health": self._npu_health,
            "current_model": self._current_model,
            "models": {key: model.get_stats() for key, model in self._models.items()},
            "total_inferences": self._total_inferences,
            "total_errors": self._total_errors,
            "alert_level": self._alert_level,
        }

    # ================================================================
    # 调度核心
    # ================================================================

    def _scheduler_loop(self):
        """主调度循环"""
        while self._running:
            try:
                # 1. 选择下一个模型
                model_key = self._select_model()
                if model_key is None:
                    time.sleep(0.01)
                    continue

                model = self._models[model_key]
                if not model.is_loaded:
                    continue

                # 2. 准备输入
                input_tensor = self._prepare_input(model_key)
                if input_tensor is None:
                    time.sleep(0.01)
                    continue

                # 3. 执行推理
                self._current_model = model_key
                outputs = model.infer(input_tensor)

                # 4. 处理结果
                if outputs is not None and len(outputs) > 0:
                    self._total_inferences += 1
                    self._process_result(model_key, outputs)
                else:
                    self._total_errors += 1
                    self._npu_error_count += 1

            except Exception as e:
                self._total_errors += 1
                self._npu_error_count += 1
                logger.error(f"调度器异常: {e}")
                if self._npu_error_count > 10:
                    self._npu_health = "error"
                time.sleep(0.1)

    def _select_model(self) -> Optional[str]:
        """
        优先级+时间片选择

        规则:
          1. 按优先级排序 (P0 > P1 > P2)
          2. 跳过不可用/未加载的模型
          3. 跳过在冷却期内的模型
        """
        now = time.time()
        candidates = []

        for key, model in self._models.items():
            if not model.is_loaded:
                continue
            elapsed = now - model._last_run
            if elapsed >= model.interval:
                candidates.append((model.priority, key))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _prepare_input(self, model_key: str):
        """准备模型输入 tensor"""
        import numpy as np

        # 尝试从摄像头抓帧
        frame_path = NPU_FRAME_PATH
        if not os.path.exists(frame_path):
            try:
                from camera_capture import capture_frame
                capture_frame()
            except Exception:
                pass

        if not os.path.exists(frame_path):
            return None

        try:
            import cv2
            img = cv2.imread(frame_path)
            if img is None:
                return None
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img_rgb = cv2.resize(img_rgb, (640, 640))
            img_float = img_rgb.astype(np.float32) / 255.0
            return np.expand_dims(img_float, axis=0)  # [1, 640, 640, 3]
        except Exception as e:
            logger.debug(f"准备输入失败: {e}")
            return None

    # ================================================================
    # 结果处理
    # ================================================================

    def _process_result(self, model_key: str, outputs: list):
        """处理推理结果 → 写入 NavState"""
        try:
            if model_key == "yolo":
                self._process_yolo(outputs)
            elif model_key == "lane":
                self._process_lane(outputs)
            elif model_key == "sign":
                self._process_sign(outputs)

            with state as s:
                s.npu_health = self._npu_health
                s.active_npu_model = model_key

        except Exception as e:
            logger.debug(f"处理结果失败 ({model_key}): {e}")

    def _process_yolo(self, outputs: list):
        """处理 YOLOv5 输出 → 车辆检测 + 危险评估"""
        # 复用 ai_detect 的后处理逻辑
        from ai_detect import _parse_yolov5_output, _parse_yolov8_output

        # YOLOv5: 3 个 tensor (三尺度输出), YOLOv8: 1 个 tensor
        if len(outputs) >= 3:
            # YOLOv5 多尺度输出: [1,255,80,80], [1,255,40,40], [1,255,20,20]
            detections = _parse_yolov5_output(outputs)
        elif len(outputs) == 1 and len(outputs[0].shape) >= 3:
            if outputs[0].shape[1] == 84:
                detections = _parse_yolov8_output(outputs[0])
            else:
                detections = _parse_yolov5_output(outputs)
        else:
            return

        # 过滤车辆类别
        from ai_detect import VEHICLE_CLASSES
        vehicle_dets = []
        for d in detections:
            if d["cls_id"] in VEHICLE_CLASSES and d["confidence"] > 0.5:
                vehicle_dets.append(d)

        self._yolo_history.append(vehicle_dets)
        alert = self._assess_danger(vehicle_dets)

        # 写入 NavState
        with state as s:
            s.ai_alert_level = alert["level"]
            s.ai_alert_msg = alert["msg"]
            if alert["level"] >= 2:
                if not s.error_message or "⚠" not in s.error_message:
                    s.error_message = alert["msg"]
            elif alert["level"] == 0:
                if s.error_message and "后方" in (s.error_message or ""):
                    s.error_message = ""

    def _box_area(self, d: dict) -> float:
        """从检测结果计算 box 面积 (归一化到 0-1)"""
        w = d["x2"] - d["x1"]
        h = d["y2"] - d["y1"]
        return w * h / (640 * 640)  # 640x640 输入

    def _assess_danger(self, detections: list) -> dict:
        """4 级危险评估 (与 ai_detect.py 逻辑一致)"""
        vehicles = [d for d in detections if d["cls_id"] in (2, 5, 7, 3)]
        n_vehicles = len(vehicles)

        if n_vehicles == 0:
            self._alert_level = max(0, self._alert_level - 1)
            return {"level": 0, "msg": "", "vehicles": 0}

        biggest = max(vehicles, key=lambda d: self._box_area(d)) if vehicles else None

        speed = 0.0
        if len(self._yolo_history) >= 2:
            prev_frames = list(self._yolo_history)[-2]
            prev_area = sum(self._box_area(d) for d in prev_frames) / max(len(prev_frames), 1)
            curr_area = sum(self._box_area(d) for d in detections) / max(len(detections), 1)
            if prev_area > 0.001:
                speed = (curr_area - prev_area) / prev_area

        level = 1  # ALERT_WATCH
        msg = f"后方{n_vehicles}辆车"

        if speed > 0.03:
            level = 3  # ALERT_DANGER
            cls_name = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
            bname = cls_name.get(biggest["cls_id"], "车辆") if biggest else "车辆"
            msg = f"⚠ 车辆快速接近! {bname}"
        elif speed > 0.01:
            level = 2  # ALERT_WARNING
            msg = f"后方{n_vehicles}辆车接近中"
        elif biggest and self._box_area(biggest) > 0.3:
            level = 2
            msg = "车辆距离较近"

        self._alert_level = level
        self._alert_msg = msg
        return {"level": level, "msg": msg, "vehicles": n_vehicles,
                "speed_index": round(speed, 4)}

    def _process_lane(self, outputs: list):
        """处理车道/OCR 检测"""
        # TODO: 用 PPOCR / LaneNet 模型时实现
        pass

    def _process_sign(self, outputs: list):
        """处理交通标志分类"""
        # TODO: 用 ResNet / 分类模型时实现
        pass


# ============================================================
# 全局管理器
# ============================================================
_scheduler: Optional[NPUScheduler] = None


def get_scheduler() -> Optional[NPUScheduler]:
    return _scheduler


def get_scheduler_status() -> dict:
    if _scheduler is None:
        return {"active": False, "reason": "调度器未初始化"}
    return {"active": True, **_scheduler.get_status()}


def start_scheduler() -> NPUScheduler:
    """启动全局调度器 (main.py 调用)"""
    global _scheduler
    if _scheduler is None:
        _scheduler = NPUScheduler()
    _scheduler.start()
    return _scheduler
