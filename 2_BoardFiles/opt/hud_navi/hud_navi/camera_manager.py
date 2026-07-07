"""
摄像头管理器 — V2.1
管理 USB 摄像头生命周期: 扫描→打开→采集线程→内存帧队列→关闭

V2.1 重大改动:
  - 彻底移除 ffmpeg 依赖 (板子未预装), 改用 v4l2-ctl (已预装)
  - 持久流式采集: 启动一个 v4l2-ctl 进程持续输出 MJPEG → Python 解析帧
  - 每帧不再启动新进程, 采集频率从 ~0.5fps 提升至实际摄像头帧率
  - 新增 get_latest_jpeg() 供 HTTP /camera/frame 接口使用

设计:
  - 采集线程 daemon, v4l2-ctl --stream-to=/dev/stdout → pipe → MJPEG解析
  - 内存环形队列 deque(maxlen=3), 溢出丢旧帧
  - _stop_event 控制安全退出
  - 子进程崩溃自动重启, 带最大重试保护
"""

import subprocess
import os
import time
import threading
import logging
import select as _sel
from collections import deque

from camera_config import (
    DEVICE, WIDTH, HEIGHT, FPS, FORMAT,
    FRAME_CACHE_PATH, FRAME_QUEUE_SIZE,
    FFMPEG_RETRY_MAX, FFMPEG_RETRY_DELAY,
)

logger = logging.getLogger(__name__)

# 采集线程内部常量
_PIPE_READ_SIZE = 65536          # 每次从 pipe 读取的字节数
_PIPE_POLL_TIMEOUT = 0.1         # select() 轮询超时 (秒)
_SOI = b'\xff\xd8'               # JPEG Start Of Image
_EOI = b'\xff\xd9'               # JPEG End Of Image
_MIN_FRAME_BYTES = 500            # 有效帧最小字节数
_MAX_BUF_BYTES = 512 * 1024       # 缓冲区上限, 防止异常膨胀
_V4L2_START_TIMEOUT = 3           # v4l2-ctl 格式设置超时
_V4L2_KILL_TIMEOUT = 2            # terminate 等待超时


class CameraManager:
    """USB 摄像头管理器 (单例)"""

    def __init__(self):
        self._device = DEVICE
        self._width = WIDTH
        self._height = HEIGHT
        self._fps = FPS

        # 采集状态
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._capture_proc = None       # v4l2-ctl 持久子进程
        self._proc_restarts = 0         # 累计重启次数

        # 内存帧队列
        self._frame_lock = threading.Lock()
        self._frame_queue = deque(maxlen=FRAME_QUEUE_SIZE)
        self._frame_seq = 0             # 累计帧序号
        self._last_seq_read = 0         # AI 线程已读到的序号
        self._last_capture_time = 0.0
        self._error_count = 0
        self._consecutive_errors = 0    # 连续错误计数 (用于离线判定)

    # ================================================================
    # 摄像头发现
    # ================================================================

    def scan(self) -> list:
        """扫描系统中的摄像头设备 (不限于 v4l2-ctl --list-devices)"""
        devices = []
        for i in range(32):
            dev = f"/dev/video{i}"
            if os.path.exists(dev):
                # 尝试获取设备名称
                name = f"video{i}"
                try:
                    result = subprocess.run(
                        ["v4l2-ctl", "-d", dev, "--get-input"],
                        capture_output=True, timeout=1
                    )
                    if result.returncode == 0:
                        name = result.stdout.decode(errors="replace").strip()
                        if name:
                            name = f"video{i}: {name}"
                except Exception:
                    pass
                devices.append({"device": dev, "name": name, "available": True})
        return devices

    def is_detected(self) -> bool:
        return os.path.exists(self._device)

    # ================================================================
    # 生命周期
    # ================================================================

    def open(self, device: str = None) -> bool:
        """打开摄像头并启动采集线程 (幂等)"""
        if self._running:
            logger.info("摄像头已在运行, 跳过")
            return True

        if device:
            self._device = device
        if not os.path.exists(self._device):
            logger.warning(f"摄像头设备不存在: {self._device}")
            return False

        # 清理上一轮崩后残留的孤儿v4l2-ctl进程(占用设备)
        try:
            subprocess.run(['killall', '-9', 'v4l2-ctl'], capture_output=True, timeout=1)
            time.sleep(0.5)
        except Exception:
            pass

        # 检查 v4l2-ctl
        if not self._check_v4l2():
            logger.warning("v4l2-ctl 不可用, 摄像头无法工作")
            return False

        self._stop_event.clear()
        self._running = True
        self._proc_restarts = 0
        self._error_count = 0
        self._consecutive_errors = 0
        self._frame_seq = 0
        self._last_seq_read = 0

        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="Camera"
        )
        self._thread.start()
        logger.info(
            f"摄像头已打开: {self._device} {self._width}x{self._height} "
            f"MJPEG流式采集 (v4l2-ctl→pipe)"
        )
        return True

    def close(self):
        """安全停止采集, 释放所有资源"""
        self._running = False
        self._stop_event.set()

        # 1. 杀死 v4l2-ctl 子进程
        if self._capture_proc and self._capture_proc.poll() is None:
            try:
                self._capture_proc.terminate()
                self._capture_proc.wait(timeout=_V4L2_KILL_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    self._capture_proc.kill()
                    self._capture_proc.wait()
                except Exception:
                    pass
            except Exception:
                pass

        # 2. 关闭 stdout pipe (防止采集线程阻塞在 read)
        if self._capture_proc and self._capture_proc.stdout:
            try:
                self._capture_proc.stdout.close()
            except Exception:
                pass
        self._capture_proc = None

        # 3. 等待采集线程退出
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

        # 4. 清空帧队列
        with self._frame_lock:
            self._frame_queue.clear()

        # 5. 清理磁盘缓存
        try:
            if os.path.exists(FRAME_CACHE_PATH):
                os.remove(FRAME_CACHE_PATH)
        except Exception:
            pass

        logger.info("摄像头已关闭")

    # ================================================================
    # 帧获取 (AI 线程 / HTTP 线程调用)
    # ================================================================

    def get_latest_frame(self) -> dict:
        """获取最新未处理帧 (非阻塞), 返回 dict 或 None"""
        with self._frame_lock:
            if not self._frame_queue:
                return None
            # 取最新一帧, 跳过已读序号
            latest = self._frame_queue[0]
            if latest["seq"] <= self._last_seq_read:
                return None
            self._last_seq_read = latest["seq"]
            return dict(latest)

    def get_latest_jpeg(self) -> bytes:
        """返回最新帧的 JPEG 数据, 供 HTTP /camera/frame 响应 (非阻塞)"""
        with self._frame_lock:
            if not self._frame_queue:
                return None
            return bytes(self._frame_queue[0]["data"])

    # ================================================================
    # 状态查询
    # ================================================================

    def get_status(self) -> dict:
        with self._frame_lock:
            queue_len = len(self._frame_queue)
        return {
            "device": self._device,
            "detected": os.path.exists(self._device),
            "running": self._running,
            "resolution": f"{self._width}x{self._height}",
            "fps_target": self._fps,
            "format": FORMAT,
            "frame_seq": self._frame_seq,
            "last_capture": round(self._last_capture_time, 1),
            "error_count": self._error_count,
            "proc_restarts": self._proc_restarts,
            "consecutive_errors": self._consecutive_errors,
            "queue_len": queue_len,
            "queue_max": FRAME_QUEUE_SIZE,
            "method": "v4l2-ctl-stream",
        }

    # ================================================================
    # 内部: 环境检查
    # ================================================================

    def _check_v4l2(self) -> bool:
        """检查 v4l2-ctl 是否可用"""
        try:
            subprocess.run(
                ["v4l2-ctl", "--version"],
                capture_output=True, timeout=2
            )
            return True
        except Exception:
            return False

    # ================================================================
    # 内部: 持久流式采集进程
    # ================================================================

    def _start_capture_proc(self) -> bool:
        """
        启动 v4l2-ctl 持续流式进程.
        输出 MJPEG 帧到 stdout pipe, Python 从 pipe 解析.
        """
        try:
            # Step 1: 设置摄像头格式 (MJPEG 640x480)
            subprocess.run([
                "v4l2-ctl", "-d", self._device,
                "--set-fmt-video",
                f"width={self._width},height={self._height},pixelformat=MJPG"
            ], capture_output=True, timeout=_V4L2_START_TIMEOUT)

            # Step 2: 启动流式输出到 stdout
            # --stream-mmap: 使用内存映射缓冲区 (更高效)
            # --stream-to=/dev/stdout: 原始帧数据输出到标准输出
            self._capture_proc = subprocess.Popen([
                "v4l2-ctl", "-d", self._device,
                "--stream-mmap",
                "--stream-to=/dev/stdout"
            ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            self._proc_restarts += 1
            logger.info(f"v4l2-ctl 流式进程已启动 (第{self._proc_restarts}次)")
            return True

        except Exception as e:
            logger.error(f"v4l2-ctl 启动失败: {e}")
            return False

    # ================================================================
    # 内部: 采集主循环
    # ================================================================

    def _capture_loop(self):
        """
        后台采集主循环.
        从 v4l2-ctl stdout pipe 读取 MJPEG 流 → 按 SOI/EOI 切帧 → 入队.
        """
        buf = b""  # MJPEG 流接收缓冲

        while not self._stop_event.is_set():
            # ---- 进程健康检查 + 自动重启 ----
            if self._capture_proc is None or self._capture_proc.poll() is not None:
                if self._capture_proc is not None:
                    rc = self._capture_proc.poll()
                    logger.warning(f"v4l2-ctl 进程退出 (退出码={rc}), 准备重启...")
                    try:
                        if self._capture_proc.stdout:
                            self._capture_proc.stdout.close()
                    except Exception:
                        pass
                    self._capture_proc = None

                if self._consecutive_errors >= FFMPEG_RETRY_MAX:
                    logger.error(
                        f"v4l2-ctl 连续重启失败{FFMPEG_RETRY_MAX}次, 采集线程退出"
                    )
                    break

                if not self._start_capture_proc():
                    logger.error("v4l2-ctl 启动失败, 1s后重试...")
                    self._stop_event.wait(1.0)
                    continue

                buf = b""  # 清空残留缓冲

            # ---- 从 pipe 读取数据 ----
            try:
                # select: 非阻塞检查 + 超时控制
                ready, _, _ = _sel.select(
                    [self._capture_proc.stdout], [], [], _PIPE_POLL_TIMEOUT
                )
                if not ready:
                    continue

                chunk = self._capture_proc.stdout.read(_PIPE_READ_SIZE)
                if not chunk:
                    # pipe EOF: 进程已死, 下一轮循环自动重启
                    continue

                buf += chunk

            except (ValueError, OSError) as e:
                # pipe 已关闭 或 进程已死
                logger.debug(f"pipe 读取异常: {e}")
                continue
            except Exception as e:
                logger.debug(f"采集读取异常: {e}")
                self._consecutive_errors += 1
                self._stop_event.wait(0.1)
                continue

            # ---- 缓冲区保护: 防止异常膨胀 ----
            if len(buf) > _MAX_BUF_BYTES:
                # 保留最后一个 SOI 之后的数据
                cutoff = len(buf) - _MAX_BUF_BYTES // 2
                last_soi = buf.rfind(_SOI, 0, cutoff)
                if last_soi > 0:
                    buf = buf[last_soi:]
                else:
                    logger.warning(f"缓冲区异常 ({len(buf)}B), 已清空")
                    buf = b""
                continue

            # ---- MJPEG 帧解析: 按 SOI/EOI 提取独立 JPEG ----
            while True:
                soi = buf.find(_SOI)
                if soi == -1:
                    # 没有帧头: 丢弃非 JPEG 垃圾字节
                    if len(buf) > 256:
                        buf = b""
                    break

                eoi = buf.find(_EOI, soi + 2)
                if eoi == -1:
                    # 有帧头没帧尾: 保留帧头起的数据, 等待后续 chunk
                    if soi > 0:
                        buf = buf[soi:]
                    break

                # 提取完整帧
                eoi_end = eoi + 2
                frame_data = buf[soi:eoi_end]
                buf = buf[eoi_end:]

                # 入队校验
                if len(frame_data) >= _MIN_FRAME_BYTES:
                    now = time.time()
                    with self._frame_lock:
                        self._frame_seq += 1
                        self._last_capture_time = now
                        self._consecutive_errors = 0
                        self._frame_queue.appendleft({
                            "seq": self._frame_seq,
                            "ts": now,
                            "data": bytes(frame_data),
                        })
                # 太小: 无效帧, 静默丢弃

        # ---- 退出清理 ----
        self._running = False
        logger.info("采集线程已退出")


# ============================================================
# 全局单例
# ============================================================
camera = CameraManager()
