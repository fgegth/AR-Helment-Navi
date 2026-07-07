"""
播报辅助 — v5.1: Edge-TTS 优先 + audio_manager 音频路由 + 预录WAV降级

优先级:
  1. Edge-TTS 动态生成 -> aplay 播放 (联网, 使用 audio_manager 输出设备)
  2. 预录 WAV 文件 -> aplay 播放 (离线)
  3. 蜂鸣 beep 降级 (永远可用)
"""
import wave
import struct
import os
import subprocess
import threading
import logging

logger = logging.getLogger(__name__)

PROMPT_DIR = "/opt/hud_navi/data/voice_prompts"

PROMPTS = {
    "没有导航": "cmd_fail.wav",
    "取消":     "done.wav",
    "开始导航": "cmd_ok.wav",
    "公司":     "cmd_qugongsi.wav",
    "家":       "cmd_huijia.wav",
    "到达":     "all_done.wav",
    "超速":     None,
    "距离":     None,
}

EDGE_CACHE_DIR = "/tmp/tts_cache"

PRE_CACHE_PHRASES = [
    "我在请说",
    "开始导航",
    "前方100米左转",
    "前方200米右转",
    "前方300米掉头",
    "请沿当前道路直行",
    "您已到达目的地",
    "导航已取消",
    "剩余",
    "约",
    "分钟",
    "公里",
    "米",
]


def _get_playback_device() -> str:
    """获取当前最佳播放设备"""
    try:
        from audio_manager import get_output_device
        dev = get_output_device()
        if dev:
            return dev
    except Exception:
        pass
    return "default"


def _play_mp3(path: str):
    """播放 MP3 文件 (mpg123 → 动态音频设备)"""
    try:
        dev = _get_playback_device()
        # mpg123 -a 指定 ALSA 输出设备, -q 静默
        subprocess.run(
            ["mpg123", "-q", "-a", dev, path],
            timeout=15
        )
        return True
    except Exception:
        return False


def _play_wav(path: str):
    """播放 WAV 文件 (aplay, 使用动态音频设备)"""
    try:
        dev = _get_playback_device()
        subprocess.run(["aplay", "-q", "-D", dev, path], timeout=5)
        return True
    except Exception:
        return False


def _make_beep(path: str, freq: int = 440, duration: float = 0.3):
    """生成提示音 WAV (立体声, 适配 rk809 codec)"""
    import math
    sr = 16000
    frames = []
    for i in range(int(sr * duration)):
        val = int(8000 * math.sin(2 * math.pi * freq * i / sr))
        frames.append(struct.pack("<hh", val, val))  # 双声道
    wf = wave.open(path, "wb")
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(sr)
    wf.writeframes(b"".join(frames))
    wf.close()


# Edge-TTS 单例 (避免每次 speak 重建实例+网络检查)
_tts_instance = None
_tts_import_checked = False

def _get_tts():
    """获取 Edge-TTS 单例 (延迟初始化 + 导入失败不再重试)"""
    global _tts_instance, _tts_import_checked
    if _tts_instance is None and not _tts_import_checked:
        try:
            from tts_edge import EdgeTTS
            from config import TTS_EDGE_VOICE
            _tts_instance = EdgeTTS(voice=TTS_EDGE_VOICE, cache_dir=EDGE_CACHE_DIR)
            _tts_import_checked = True
        except (ImportError, AttributeError):
            _tts_instance = False  # tts_edge 模块不存在或配置缺失
            _tts_import_checked = True
    return _tts_instance if _tts_instance is not False else None

def _edge_speak(text: str) -> bool:
    """Edge-TTS 生成并播放 (使用动态音频设备, 复用单例)"""
    tts = _get_tts()
    if tts is None:
        return False
    try:
        mp3_path = tts.save_to_file(text)
        if mp3_path:
            return _play_mp3(mp3_path)
    except Exception as e:
        logger.debug("Edge-TTS: %s", e)
    return False


def speak(text: str):
    """
    播报文字 (自动选择最佳方案)
    策略: Edge-TTS -> 预录WAV -> 蜂鸣
    """
    # 1. 尝试 Edge-TTS
    if _edge_speak(text):
        return

    # 2. 尝试预录 WAV
    fname = None
    for key, val in PROMPTS.items():
        if key in text:
            fname = val
            break

    if fname:
        path = os.path.join(PROMPT_DIR, fname)
        if os.path.exists(path):
            _play_wav(path)
            return

    # 3. 蜂鸣降级
    _make_beep("/tmp/speak_beep.wav")
    _play_wav("/tmp/speak_beep.wav")


def speak_async(text: str):
    """异步播报 (不阻塞主线程)"""
    threading.Thread(target=speak, args=(text,), daemon=True).start()


def pre_cache_phrases():
    """后台预生成高频导航语音缓存 (系统启动时调用一次)"""
    try:
        tts = _get_tts()
        if tts is None:
            return
        if not os.path.exists(EDGE_CACHE_DIR):
            os.makedirs(EDGE_CACHE_DIR, exist_ok=True)

        logger.info("预缓存导航语音...")
        for phrase in PRE_CACHE_PHRASES:
            tts.save_to_file(phrase)
        logger.info("语音预缓存完成 (%d条)", len(PRE_CACHE_PHRASES))
    except Exception as e:
        logger.debug("预缓存跳过: %s", e)
