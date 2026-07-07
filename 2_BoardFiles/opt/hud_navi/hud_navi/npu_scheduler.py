"""
NPU 多模型时分复用调度器
管理 RK3568 NPU (1 TOPS) 上的多个推理模型

调度策略: 优先级抢占 + 时间片轮转
  P0 (安全): YOLOv5s 车辆检测    5Hz
  P1 (导航): 车道线检测          10Hz
  P2 (信息): 交通标志分类        2Hz

与现有 ai_detect.py 向后兼容: 调度器激活时替代直接 ai_detect 调用
"""
import os
import time
import json
import logging
import threading
import subprocess
from collections import deque
from typing import Optional, Dict, Any, List

from config import (
    NPU_MODEL_YOLO, NPU_MODEL_LANE, NPU_MODEL_SIGN,
    NPU_YOLO_PRIORITY, NPU_LANE_PRIORITY, NPU_SIGN_PRIORITY,
    NPU_YOLO_INTERVAL, NPU_LANE_INTERVAL, NPU_SIGN_INTERVAL,
    NPU_SWITCH_TIMEOUT, NPU_INFERENCE_TIMEOUT, NPU_FRAME_PATH,
)
from nav_state import state

logger = logging.getLogger(__name__)

# ============================================================
# 模型配置
# ============================================================

MODEL_REGISTRY = {
    "yolo": {
        "name": "YOLOv5s 车辆检测",
        "priority": NPU_YOLO_PRIORITY,
        "interval": NPU_YOLO_INTERVAL,
        "model_path": NPU_MODEL_YOLO,
        "binary": "/data/yolo/rknn_yolov5_demo",
        "lib_path": "/data/yolo/lib",
        "labels": "/data/yolo/model/coco_80_labels_list.txt",
        "vehicle_classes": {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"},
    },
    "lane": {
        "name": "车道线检测",
        "priority": NPU_LANE_PRIORITY,
        "interval": NPU_LANE_INTERVAL,
        "model_path": NPU_MODEL_LANE,
        "binary": "/data/npu/rknn_lane_demo",
        "lib_path": "/data/npu/lib",
    },
    "sign": {
        "name": "交通标志分类",
        "priority": NPU_SIGN_PRIORITY,
        "interval": NPU_SIGN_INTERVAL,
        "model_path": NPU_MODEL_SIGN,
        "binary": "/data/npu/rknn_classify_demo",
        "lib_path": "/data/npu/lib",
    },
}


class NPUScheduler:
    """
    NPU 多模型调度器
    在独立守护线程中运行, 按优先级+时间片调度模型推理
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 模型状态追踪
        self._last_run: Dict[str, float] = {}  # model_key → 上次运行时间
        self._current_model: str = ""           # 当前加载的模型
        self._model_available: Dict[str, bool] = {}  # 模型文件是否可用
        self._inference_count: Dict[str, int] = {}   # 推理计数

        # YOLOv5 车辆检测结果缓存
        self._yolo_history = deque(maxlen=5)  # 最近5帧检测结果
        self._alert_level: int = 0
        self._alert_msg: str = ""

        # 车道检测结果缓存
        self._lane_history = deque(maxlen=3)
        # 交通标志结果缓存
        self._sign_history = deque(maxlen=10)

        # NPU 健康状态
        self._npu_health: str = "ok"
        self._npu_error_count: int = 0
        self._total_inferences: int = 0
        self._total_errors: int = 0

        # 验证模型文件
        self._check_models()

    def _check_models(self):
        """检查各模型文件和二进制是否可用"""
        available_count = 0
        for key, cfg in MODEL_REGISTRY.items():
            model_ok = os.path.exists(cfg["model_path"])
            binary_ok = os.path.exists(cfg["binary"])
            avail = model_ok and binary_ok
            self._model_available[key] = avail
            self._last_run[key] = 0.0
            self._inference_count[key] = 0
            if avail:
                available_count += 1
                logger.info(f"  NPU模型 {cfg['name']}: 可用")
            else:
                logger.warning(f"  NPU模型 {cfg['name']}: 不可用 "
                              f"(model={model_ok}, binary={binary_ok})")

        if available_count == 0:
            self._npu_health = "error"
            logger.warning("无可用NPU模型, 调度器将空转")
        elif available_count < len(MODEL_REGISTRY):
            self._npu_health = "degraded"

    # ================================================================
    # 公开接口
    # ================================================================

    def start(self):
        if self._running:
            return
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
        logger.info(f"NPU 调度器已停止 (总推理{self._total_inferences}次, "
                    f"错误{self._total_errors}次)")

    def get_status(self) -> dict:
        """获取调度器状态"""
        return {
            "health": self._npu_health,
            "current_model": self._current_model,
            "models": {
                key: {
                    "available": self._model_available.get(key, False),
                    "inferences": self._inference_count.get(key, 0),
                    "last_run_ago": round(time.time() - self._last_run.get(key, 0), 2),
                }
                for key in MODEL_REGISTRY
            },
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
                # 1. 选择下一个要执行的模型
                model_key = self._select_model()
                if model_key is None:
                    # 所有模型都在冷却中，短暂休眠
                    time.sleep(0.01)
                    continue

                # 2. 执行推理
                result = self._run_inference(model_key)
                self._inference_count[model_key] += 1
                self._total_inferences += 1
                self._last_run[model_key] = time.time()

                # 3. 处理结果 → 写入 NavState
                if result is not None:
                    self._process_result(model_key, result)

            except Exception as e:
                self._total_errors += 1
                self._npu_error_count += 1
                logger.error(f"调度器异常: {e}")
                if self._npu_error_count > 10:
                    self._npu_health = "error"
                time.sleep(0.1)

    def _select_model(self) -> Optional[str]:
        """
        基于优先级+时间片选择下一个模型

        规则:
          1. 按优先级排序 (P0 > P1 > P2)
          2. 跳过不可用的模型
          3. 跳过在冷却期内的模型
          4. 返回最优先的可执行模型
        """
        now = time.time()
        candidates = []

        for key, cfg in MODEL_REGISTRY.items():
            if not self._model_available.get(key, False):
                continue
            elapsed = now - self._last_run.get(key, 0.0)
            if elapsed >= cfg["interval"]:
                candidates.append((cfg["priority"], key))

        if not candidates:
            return None

        # 按优先级排序 (数值越小优先级越高)
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _run_inference(self, model_key: str) -> Optional[dict]:
        """
        执行单个模型的推理

        使用子进程模式 (与现有 ai_detect.py 兼容):
          subprocess.run([binary, model_path, image_path], ...)
        """
        cfg = MODEL_REGISTRY[model_key]
        self._current_model = model_key

        # 检查输入帧是否可用 (对于需要摄像头的模型)
        if model_key in ("yolo", "lane", "sign"):
            if not os.path.exists(NPU_FRAME_PATH):
                # 尝试抓一帧
                self._capture_frame()

        image_path = NPU_FRAME_PATH
        if not os.path.exists(image_path):
            return None

        try:
            env = os.environ.copy()
            env["LD_LIBRARY_PATH"] = cfg["lib_path"] + ":" + env.get("LD_LIBRARY_PATH", "")

            result = subprocess.run(
                [cfg["binary"], cfg["model_path"], image_path],
                cwd=os.path.dirname(cfg["binary"]),
                env=env,
                capture_output=True,
                timeout=NPU_INFERENCE_TIMEOUT,
            )

            output = result.stdout.decode("utf-8", errors="ignore")
            stderr = result.stderr.decode("utf-8", errors="ignore")

            if result.returncode != 0:
                logger.debug(f"推理失败 ({model_key}): {stderr[:100]}")
                return None

            # 解析输出 (各模型格式不同, 用模型特定的解析器)
            parsed = self._parse_output(model_key, output)
            return parsed

        except subprocess.TimeoutExpired:
            logger.warning(f"推理超时 ({model_key})")
            return None
        except FileNotFoundError:
            logger.error(f"推理二进制文件丢失 ({model_key}): {cfg['binary']}")
            self._model_available[model_key] = False
            return None
        except Exception as e:
            logger.error(f"推理异常 ({model_key}): {e}")
            return None

    def _capture_frame(self):
        """抓取摄像头帧 (复用 camera_capture 模块)"""
        try:
            from camera_capture import capture_frame
            capture_frame()
        except Exception:
            pass

    # ================================================================
    # 输出解析
    # ================================================================

    def _parse_output(self, model_key: str, output: str) -> Optional[dict]:
        """解析模型输出"""
        if model_key == "yolo":
            return self._parse_yolo(output)
        elif model_key == "lane":
            return self._parse_lane(output)
        elif model_key == "sign":
            return self._parse_sign(output)
        return None

    def _parse_yolo(self, output: str) -> dict:
        """解析 YOLOv5 检测输出"""
        detections = []
        for line in output.strip().split("\n"):
            parts = line.strip().split()
            if len(parts) >= 6:
                try:
                    cls_id = int(parts[0])
                    conf = float(parts[1])
                    x1, y1, x2, y2 = float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])
                    vehicle_classes = MODEL_REGISTRY["yolo"]["vehicle_classes"]
                    if cls_id in vehicle_classes and conf > 0.5:
                        detections.append({
                            "cls_id": cls_id,
                            "name": vehicle_classes[cls_id],
                            "confidence": conf,
                            "center_x": (x1 + x2) / 2 / 640,
                            "center_y": (y1 + y2) / 2 / 480,
                            "box_area": (x2 - x1) * (y2 - y1) / (640 * 480),
                        })
                except ValueError:
                    pass

        self._yolo_history.append(detections)
        # 评估危险等级
        alert = self._assess_danger(detections)
        return {"type": "yolo", "detections": detections, "alert": alert}

    def _assess_danger(self, detections: list) -> dict:
        """评估车辆危险等级 (复用 ai_detect.py 的4级预警逻辑)"""
        vehicles = [d for d in detections if d["cls_id"] in (2, 5, 7, 3)]
        n_vehicles = len(vehicles)

        if n_vehicles == 0:
            self._alert_level = max(0, self._alert_level - 1)
            return {"level": 0, "msg": "", "vehicles": 0}

        # 找最大车辆
        biggest = max(vehicles, key=lambda d: d["box_area"]) if vehicles else None

        # 接近速度估算 (从多帧面积变化)
        speed = 0.0
        if len(self._yolo_history) >= 2:
            prev_frames = list(self._yolo_history)[-2]
            prev_area = sum(d["box_area"] for d in prev_frames) / max(len(prev_frames), 1)
            curr_area = sum(d["box_area"] for d in detections) / max(len(detections), 1)
            if prev_area > 0.001:
                speed = (curr_area - prev_area) / prev_area

        level = 1  # ALERT_WATCH
        msg = f"后方{n_vehicles}辆车"

        if speed > 0.03:
            level = 3  # ALERT_DANGER
            msg = f"⚠ 车辆快速接近! {biggest['name'] if biggest else ''}"
        elif speed > 0.01:
            level = 2  # ALERT_WARNING
            msg = f"后方{n_vehicles}辆车接近中"
        elif biggest and biggest["box_area"] > 0.3:
            level = 2
            msg = "车辆距离较近"

        self._alert_level = level
        self._alert_msg = msg
        return {"level": level, "msg": msg, "vehicles": n_vehicles,
                "speed_index": round(speed, 4)}

    def _parse_lane(self, output: str) -> dict:
        """解析车道线检测输出"""
        lanes = []
        for line in output.strip().split("\n"):
            parts = line.strip().split()
            if len(parts) >= 3:
                try:
                    lane_type = parts[0]  # "left" / "right" / "center"
                    confidence = float(parts[1])
                    # 后续为点坐标序列 x1,y1,x2,y2,...
                    points = []
                    for i in range(2, len(parts) - 1, 2):
                        points.append((float(parts[i]), float(parts[i + 1])))
                    if confidence > 0.5:
                        lanes.append({"type": lane_type, "confidence": confidence, "points": points})
                except (ValueError, IndexError):
                    pass

        self._lane_history.append(lanes)
        return {"type": "lane", "lanes": lanes}

    def _parse_sign(self, output: str) -> dict:
        """解析交通标志分类输出"""
        signs = []
        for line in output.strip().split("\n"):
            parts = line.strip().split()
            if len(parts) >= 2:
                try:
                    sign_class = parts[0]
                    confidence = float(parts[1])
                    if confidence > 0.6:
                        signs.append({"class": sign_class, "confidence": confidence})
                except ValueError:
                    pass

        self._sign_history.append(signs)
        return {"type": "sign", "signs": signs}

    # ================================================================
    # 结果处理 → NavState
    # ================================================================

    def _process_result(self, model_key: str, result: dict):
        """将推理结果写入 NavState"""
        try:
            if model_key == "yolo" and "alert" in result:
                alert = result["alert"]
                with state as s:
                    s.ai_alert_level = alert["level"]
                    s.ai_alert_msg = alert["msg"]
                    s.active_npu_model = "yolo"
                    # AI预警 → 写入 error_message (HUD L10 显示)
                    if alert["level"] >= 2:
                        if not s.error_message or "⚠" not in s.error_message:
                            s.error_message = alert["msg"]
                    elif alert["level"] == 0:
                        if s.error_message == alert["msg"] or \
                           (s.error_message and "后方" in s.error_message):
                            s.error_message = ""

            elif model_key == "lane" and "lanes" in result:
                with state as s:
                    s.lane_detection_raw = {
                        "lanes": result["lanes"],
                        "timestamp": time.time(),
                    }
                    s.active_npu_model = "lane"

            elif model_key == "sign" and "signs" in result:
                with state as s:
                    s.traffic_signs = result["signs"]
                    s.active_npu_model = "sign"

            # 更新 NPU 健康状态
            with state as s:
                s.npu_health = self._npu_health

        except Exception as e:
            logger.debug(f"写入NavState失败: {e}")


# ============================================================
# 全局管理器
# ============================================================

_scheduler: Optional[NPUScheduler] = None


def get_scheduler() -> Optional[NPUScheduler]:
    return _scheduler


def get_scheduler_status() -> dict:
    """获取调度器状态 (HTTP API 用)"""
    if _scheduler is None:
        return {"active": False, "reason": "调度器未初始化"}
    return {"active": True, **_scheduler.get_status()}


def start_scheduler() -> NPUScheduler:
    """启动全局调度器"""
    global _scheduler
    if _scheduler is None:
        _scheduler = NPUScheduler()
    _scheduler.start()
    return _scheduler
