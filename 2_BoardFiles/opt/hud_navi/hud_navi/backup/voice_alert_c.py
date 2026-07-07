"""
语音播报模块 — gTTS 真人语音 + ALSA 降级蜂鸣
在线: gTTS 生成 MP3 → pygame.mixer 播放 (真人女声)
离线: ALSA 蜂鸣降级
"""
import logging
import threading
import queue
import os
import io
import tempfile

logger = logging.getLogger(__name__)

# 尝试导入
try:
    import pygame
    _pygame_ok = True
except ImportError:
    _pygame_ok = False

try:
    from gtts import gTTS
    _gtts_ok = True
except ImportError:
    _gtts_ok = False


class VoiceAlert:
    def __init__(self):
        self._running = False
        self._thread = None
        self._queue = queue.Queue(maxsize=10)
        self._last_spoken = ""
        self._last_time = 0
        self._audio_ok = os.path.exists("/usr/bin/aplay") or _pygame_ok

        # MP3 缓存: 文本 → 路径, 避免重复请求 Google
        self._cache_dir = "/tmp/tts_cache"
        os.makedirs(self._cache_dir, exist_ok=True)

        if _pygame_ok:
            try:
                pygame.mixer.init()
            except Exception:
                pass

    def init_tts(self) -> bool:
        if _gtts_ok:
            logger.info("TTS: gTTS 真人语音 就绪")
        elif _pygame_ok:
            logger.info("TTS: pygame 蜂鸣降级 就绪")
        elif self._audio_ok:
            logger.info("TTS: ALSA 蜂鸣降级 就绪")
        else:
            logger.warning("TTS: 音频不可用")
            return False
        return True

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._play_loop, daemon=True, name="Voice"
        )
        self._thread.start()

    def stop(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=3.0)

    def speak(self, text: str, force: bool = False):
        import time
        if not force and text == self._last_spoken:
            if time.time() - self._last_time < 5.0:
                return
        try:
            self._queue.put_nowait(text)
            self._last_spoken = text
            self._last_time = time.time()
        except queue.Full:
            pass

    def _play_loop(self):
        while self._running:
            try:
                text = self._queue.get(timeout=1.0)
                if text is None:
                    break
                logger.info(f"语音播报: {text}")
                if _gtts_ok:
                    self._speak_gtts(text)
                elif _pygame_ok:
                    self._speak_beep(text)
                else:
                    self._speak_alsa(text)
            except queue.Empty:
                continue

    # ---- gTTS 真人语音 ----
    def _speak_gtts(self, text: str):
        cache_file = os.path.join(self._cache_dir, text.replace("/", "_")[:60] + ".mp3")
        try:
            # 有缓存直接用
            if not os.path.exists(cache_file):
                tts = gTTS(text=text, lang="zh-cn", slow=False)
                tts.save(cache_file)

            if _pygame_ok:
                pygame.mixer.music.load(cache_file)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(100)
            else:
                # 用 ffmpeg 转 WAV → aplay
                pass
        except Exception as e:
            logger.error(f"gTTS 失败: {e}")
            self._speak_beep(text)

    # ---- 蜂鸣降级 ----
    def _speak_beep(self, text: str):
        import math, struct
        beeps = 1
        freq = 800
        if "左转" in text:
            beeps, freq = 2, 600
        elif "右转" in text:
            beeps, freq = 2, 1000
        elif "掉头" in text:
            beeps, freq = 3, 500
        elif "到达" in text:
            beeps, freq = 4, 1200

        for _ in range(beeps):
            try:
                tone = self._gen_tone(freq, 0.15, 44100)
                sound = pygame.mixer.Sound(buffer=tone)
                sound.play()
                pygame.time.wait(200)
            except Exception:
                pass

    def _speak_alsa(self, text: str):
        import math, struct, subprocess
        beeps = 1
        freq = 800
        if "左转" in text:
            beeps, freq = 2, 600
        elif "右转" in text:
            beeps, freq = 2, 1000
        elif "掉头" in text:
            beeps, freq = 3, 500
        elif "到达" in text:
            beeps, freq = 4, 1200
        for _ in range(beeps):
            try:
                subprocess.run(
                    ["aplay", "-q", "-t", "raw", "-f", "S16_LE",
                     "-r", "44100", "-c", "1", "-d", "0.15"],
                    input=self._gen_tone(freq, 0.15, 44100), timeout=1,
                )
            except Exception:
                pass

    def _gen_tone(self, freq: int, duration: float, rate: int) -> bytes:
        import math, struct
        samples = int(rate * duration)
        data = bytearray()
        for i in range(samples):
            val = int(32767 * 0.5 * math.sin(2 * math.pi * freq * i / rate))
            data.extend(struct.pack("<h", val))
        return bytes(data)
