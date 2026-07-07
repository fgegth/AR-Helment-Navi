"""
Edge-TTS 文字转语音 — 微软免费 TTS 服务
替代 gTTS (国内被墙), 质量更好、声音更自然

支持多种中文语音:
  - zh-CN-XiaoxiaoNeural     (晓晓, 女声, 活泼, 推荐导航用)
  - zh-CN-YunxiNeural        (云希, 男声)
  - zh-CN-XiaoyiNeural       (晓伊, 女声, 温柔)
  - zh-CN-YunjianNeural      (云健, 男声, 沉稳)

用法:
  tts = EdgeTTS()
  tts.speak("开始导航去公司")       # 同步播放
  tts.save_to_file("前方300米左转", "/tmp/tts_cache/nav.mp3")  # 保存文件
"""

import os
import sys
import json
import tempfile
import subprocess
import logging
import hashlib
import asyncio
import socket
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 检测 edge-tts 是否可用
try:
    import edge_tts
    _edge_tts_available = True
    EDGE_VOICES = getattr(edge_tts, 'list_voices', None)
except ImportError:
    _edge_tts_available = False


def _force_ipv4_for_edge_tts():
    """
    临时强制 IPv4 (仅用于 edge-tts 调用期间)
    板子 WiFi IPv6 到微软 Azure TTS 服务器不稳定，会 Connection reset。
    通过局部猴子补丁 socket.getaddrinfo 实现，调用完成后立即恢复。
    """
    _orig = socket.getaddrinfo

    def _v4only(host, port, family=0, type=0, proto=0, flags=0):
        return _orig(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _v4only
    return _orig  # 返回原始函数供调用方恢复


def check_edge_tts():
    """检查 edge-tts 是否已安装, 未安装则提示"""
    if not _edge_tts_available:
        logger.warning("Edge-TTS 未安装, 运行: pip install edge-tts")
        logger.warning("降级使用 gTTS 或蜂鸣音")
    return _edge_tts_available


def _run_async(coro):
    """在当前线程中运行异步协程并返回结果 (Python 3.13 兼容)"""
    try:
        # Python 3.10+: 推荐直接用 asyncio.run()
        return asyncio.run(coro)
    except RuntimeError:
        # 如果已有运行中的事件循环, 在新线程中运行
        result = [None, None]

        def _run():
            try:
                result[0] = asyncio.run(coro)
            except Exception as e:
                result[1] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=30)
        if result[1]:
            raise result[1]
        return result[0]


class EdgeTTS:
    """Edge-TTS 封装 — mp3 缓存 + 同步/异步播放"""

    def __init__(self, voice: str = None, cache_dir: str = "/tmp/tts_cache"):
        from config import TTS_EDGE_VOICE

        self._voice = voice or TTS_EDGE_VOICE
        self._cache_dir = cache_dir
        self._available = _edge_tts_available

        # 确保缓存目录存在
        os.makedirs(self._cache_dir, exist_ok=True)

        # 音频播放器检测
        self._player = self._detect_player()

        if self._available:
            logger.info(f"Edge-TTS 就绪, 语音: {self._voice}")
        else:
            logger.warning("Edge-TTS 不可用, 请安装: pip install edge-tts")

    def is_available(self) -> bool:
        return self._available

    def _detect_player(self) -> str:
        """检测可用的音频播放器 (Linux)"""
        # aplay (ALSA)
        if sys.platform != "win32":
            if os.path.exists("/usr/bin/aplay") or os.path.exists("/usr/sbin/aplay"):
                return "aplay"
            # ffplay
            if os.path.exists("/usr/bin/ffplay"):
                return "ffplay"
            # mpg123
            if os.path.exists("/usr/bin/mpg123"):
                return "mpg123"
        # Windows 用系统默认
        if sys.platform == "win32":
            return "default"
        return "aplay"  # 默认尝试 aplay

    def _cache_key(self, text: str) -> str:
        """生成缓存文件名 (基于文本 hash)"""
        h = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
        voice_short = self._voice.replace("zh-CN-", "").replace("Neural", "").lower()
        return os.path.join(self._cache_dir, f"{voice_short}_{h}.mp3")

    async def _generate_async(self, text: str, output_path: str) -> bool:
        """异步生成 MP3 文件 (带重试 + 局部IPv4强制)"""
        # 强制IPv4: 板子WiFi IPv6到微软Azure不稳定, 仅影响本次调用
        orig_getaddrinfo = _force_ipv4_for_edge_tts()
        try:
            last_err = None
            for attempt in range(3):
                try:
                    communicate = edge_tts.Communicate(text, self._voice)
                    await communicate.save(output_path)
                    logger.info(f"Edge-TTS 生成: {text[:30]} -> {output_path}")
                    return True
                except Exception as e:
                    last_err = e
                    if attempt < 2:
                        logger.warning(f"Edge-TTS retry {attempt+1}/3: {e}")
                        await asyncio.sleep(1)
            logger.error(f"Edge-TTS 生成失败(3次重试): {last_err}")
            return False
        finally:
            socket.getaddrinfo = orig_getaddrinfo  # 恢复, 不影响其他模块

    def _generate(self, text: str, output_path: str) -> bool:
        """同步生成 MP3 文件"""
        return _run_async(self._generate_async(text, output_path))

    def save_to_file(self, text: str, output_path: str = None) -> str:
        """
        生成语音 MP3 并保存到文件
        返回: 文件路径, 失败返回 None
        """
        if not self._available:
            return None

        if output_path is None:
            output_path = self._cache_key(text)

        # 有缓存直接用
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.debug(f"TTS 缓存命中: {output_path}")
            return output_path

        if self._generate(text, output_path):
            return output_path
        return None

    def speak(self, text: str, blocking: bool = False) -> bool:
        """
        生成并播放语音

        播放策略:
          - Linux (aplay): ffmpeg 转 pcm → aplay
          - Windows: 系统默认播放器
        """
        mp3_path = self.save_to_file(text)
        if mp3_path is None:
            return False

        try:
            if self._player == "aplay":
                # 用 ffmpeg 将 MP3 转为 PCM 管道给 aplay
                cmd = (
                    f"ffmpeg -v quiet -i '{mp3_path}' -f s16le -acodec pcm_s16le -ar 22050 -ac 1 -"
                )
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True
                )
                subprocess.run(
                    ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", "22050", "-c", "1"],
                    stdin=proc.stdout, timeout=15
                )
                proc.wait()
                return True

            elif self._player == "ffplay":
                subprocess.run(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", mp3_path],
                    timeout=15
                )
                return True

            elif self._player == "mpg123":
                subprocess.run(["mpg123", "-q", mp3_path], timeout=15)
                return True

            elif self._player == "default":
                # Windows: 尝试用系统默认播放器
                os.startfile(mp3_path)
                return True

        except subprocess.TimeoutExpired:
            logger.warning(f"TTS 播放超时: {text}")
        except FileNotFoundError:
            logger.warning(f"播放器 {self._player} 未找到, 请安装 ffmpeg + aplay")
        except Exception as e:
            logger.error(f"TTS 播放失败: {e}")

        return False

    def speak_file(self, mp3_path: str) -> bool:
        """直接播放已有 MP3 文件"""
        if not os.path.exists(mp3_path):
            return False

        try:
            if self._player == "aplay":
                cmd = (
                    f"ffmpeg -v quiet -i '{mp3_path}' -f s16le -acodec pcm_s16le -ar 22050 -ac 1 -"
                )
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True
                )
                subprocess.run(
                    ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", "22050", "-c", "1"],
                    stdin=proc.stdout, timeout=15
                )
                proc.wait()
                return True
            elif self._player == "mpg123":
                subprocess.run(["mpg123", "-q", mp3_path], timeout=15)
                return True
        except Exception as e:
            logger.error(f"播放失败: {e}")
        return False


# ============================================================
# 便捷函数 (替代 speak_helper.py 中功能)
# ============================================================

# 全局单例
_tts_instance = None


def get_tts() -> EdgeTTS:
    """获取 EdgeTTS 全局单例"""
    global _tts_instance
    if _tts_instance is None:
        _tts_instance = EdgeTTS()
    return _tts_instance


def speak_quick(text: str) -> bool:
    """
    便捷函数: 生成并播放语音 (非阻塞)
    适合 command 回调等场景
    """
    tts = get_tts()
    if not tts.is_available():
        return False

    # 在新线程中播放, 避免阻塞主线程
    def _play():
        tts.speak(text)

    threading.Thread(target=_play, daemon=True).start()
    return True


def generate_prompt(text: str, output_path: str) -> bool:
    """
    预生成导航语音提示 (在导航开始前生成缓存)
    适合: "左转" "右转" "到达" 等高频指令
    """
    tts = get_tts()
    return tts.save_to_file(text, output_path) is not None


# ============================================================
# 命令行测试
# ============================================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if not _edge_tts_available:
        print("Edge-TTS 未安装, 请运行: pip install edge-tts")
        sys.exit(1)

    tts = EdgeTTS()

    if len(sys.argv) > 1:
        text = sys.argv[1]
    else:
        text = "前方300米左转进入长安街"

    print(f"生成语音: {text}")
    path = tts.save_to_file(text)
    if path:
        print(f"已保存: {path}")
    else:
        print("生成失败")
