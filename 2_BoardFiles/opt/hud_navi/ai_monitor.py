"""
AI 推理调度线程 — V2.0
取最新帧 → NPU 推理 → 输出检测结果 → 回调预警层

设计:
  - 事件驱动: 取最新帧, 自动跳过已处理帧, 不轮询 sleep
  - _stop_event 安全停止
  - NPU 超时丢帧, 不阻塞采集
"""

import threading
import time
import logging

from camera_manager import camera

logger = logging.getLogger(__name__)


class AIMonitor:
    """AI 推理调度器"""

    def __init__(self):
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._callback = None          # on_detection(detection_result)
        self._last_infer_ms = 0.0      # 上一次推理耗时(ms)

    def start(self, callback=None) -> bool:
        """启动 AI 推理线程 (幂等)"""
        if self._running:
            logger.info("AI推理已在运行")
            return True
        if callback is None:
            logger.warning("AI推理启动需要回调函数")
            return False

        self._callback = callback
        self._stop_event.clear()
        self._running = True

        self._thread = threading.Thread(
            target=self._infer_loop, daemon=True, name="AI-Monitor"
        )
        self._thread.start()
        logger.info("AI推理线程已启动")
        return True

    def stop(self):
        """安全停止推理线程"""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.info("AI推理线程已停止")

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "last_infer_ms": round(self._last_infer_ms, 1),
        }

    def _infer_loop(self):
        """推理主循环: 取帧 → 检测 → 回调"""
        while not self._stop_event.is_set():
            try:
                # 取最新未处理帧 (非阻塞)
                frame = camera.get_latest_frame()
                if frame is None:
                    self._stop_event.wait(0.05)  # 无帧时短暂休眠
                    continue

                # 写磁盘缓存 (ai_detect 需要文件路径, 用独立路径防竞态)
                jpg_path = "/tmp/ai_infer_frame.jpg"
                with open(jpg_path, "wb") as f:
                    f.write(frame["data"])

                # NPU 推理
                from ai_detect import detect_vehicles, assess_danger

                t0 = time.time()
                detections = detect_vehicles(jpg_path)
                result = assess_danger(detections)
                self._last_infer_ms = (time.time() - t0) * 1000

                result["frame_seq"] = frame["seq"]
                result["frame_ts"] = frame["ts"]

                # 回调预警层
                if self._callback:
                    self._callback(result)

            except Exception as e:
                logger.debug(f"AI推理异常: {e}")

        logger.info("AI推理循环已退出")


# ============================================================
# 全局单例
# ============================================================
ai_monitor = AIMonitor()
