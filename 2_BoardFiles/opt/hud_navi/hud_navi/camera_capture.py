"""
USB 摄像头采集模块 — UVC 免驱, /dev/video0
720P MJPEG 60fps → NPU 输入 640×480
"""
import subprocess, os, struct, time, threading

DEVICE = "/dev/video0"
WIDTH, HEIGHT = 640, 480  # YOLOv5 输入分辨率

def check_camera() -> bool:
    """检测摄像头是否插好"""
    return os.path.exists(DEVICE)

def capture_frame_mjpeg(output_path: str = "/tmp/camera_frame.jpg") -> bool:
    """
    用 ffmpeg 抓一帧 MJPEG (比 OpenCV 轻)
    需要板子装了 ffmpeg (或 v4l2-ctl)
    """
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "v4l2", "-input_format", "mjpeg",
            "-video_size", f"{WIDTH}x{HEIGHT}", "-i", DEVICE,
            "-vframes", "1", output_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return os.path.exists(output_path)
    except Exception:
        return False

def capture_frame_raw(output_path: str = "/tmp/camera_frame.raw") -> bool:
    """备用: v4l2-ctl 直接抓 YUYV 原始帧"""
    try:
        subprocess.run([
            "v4l2-ctl", "-d", DEVICE, "--set-fmt-video",
            f"width={WIDTH},height={HEIGHT},pixelformat=YUYV",
            "--stream-mmap", "--stream-count=1",
            "--stream-to=" + output_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return os.path.getsize(output_path) > 0
    except Exception:
        return False

def capture_frame() -> str:
    """智能选择抓帧方式 → 返回图片路径"""
    if check_camera():
        path = "/tmp/camera_frame.jpg"
        if capture_frame_mjpeg(path):
            return path
        if capture_frame_raw(path):
            return path
    return ""
