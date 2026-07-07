"""
HUD 离线语音合成 — Piper TTS
零网络、纯本地、ARM64原生、中文真人声

用法:
  from hud_tts import HudTTS
  tts = HudTTS()
  tts.speak("前方200米右转进入紫云路")
"""
import os, subprocess, logging

logger = logging.getLogger(__name__)

PIPER_BIN = "/opt/hud_navi/piper/piper"
PIPER_MODEL = "/opt/hud_navi/piper/zh_CN-huayan-medium.onnx"
CACHE_DIR = "/tmp/tts_cache"


class HudTTS:
    """Piper TTS 封装 — 首次加载<1秒, 合成<0.5秒"""

    def __init__(self):
        self._ready = os.path.exists(PIPER_BIN) and os.path.exists(PIPER_MODEL)
        os.makedirs(CACHE_DIR, exist_ok=True)
        if self._ready:
            logger.info("HudTTS: Piper 离线语音就绪")
        else:
            logger.warning("HudTTS: Piper 未安装, 请部署 piper 二进制和模型")

    def is_ready(self) -> bool:
        return self._ready

    def synthesize(self, text: str) -> str:
        """文字→WAV, 返回路径"""
        if not text or not text.strip():
            return None
        path = os.path.join(CACHE_DIR, f"tts_{hash(text) & 0xFFFFFFFF:08x}.wav")
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            return path
        try:
            proc = subprocess.run(
                [PIPER_BIN, "--model", PIPER_MODEL, "--output_file", path],
                input=text.strip(), capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0 and os.path.exists(path):
                return path
        except Exception as e:
            logger.error(f"Piper TTS 失败: {e}")
        return None

    def speak(self, text: str, force: bool = False) -> bool:
        """生成并播放 (USB音箱 plughw:2,0, 板载 plughw:0,0)"""
        wav = self.synthesize(text)
        if not wav:
            return False
        try:
            from voice_command import audio_lock
        except ImportError:
            audio_lock = None
        try:
            if audio_lock:
                audio_lock.acquire()
            for dev in ("plughw:2,0", "plughw:0,0", "default"):
                try:
                    subprocess.run(["aplay", "-q", "-D", dev, wav], timeout=10)
                    return True
                except Exception:
                    continue
            return False
        finally:
            if audio_lock:
                audio_lock.release()
        except Exception:
            return False


_tts = None

def get_tts() -> HudTTS:
    global _tts
    if _tts is None:
        _tts = HudTTS()
    return _tts

def speak_offline(text: str) -> bool:
    return get_tts().speak(text)
