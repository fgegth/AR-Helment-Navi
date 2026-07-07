"""
语音播报模块 — gTTS(在线) / 预录WAV(离线) / 蜂鸣(降级)
三级降级: gTTS → 技术员预录人声 → ALSA 蜂鸣
"""
import logging, threading, queue, os, subprocess, time

logger = logging.getLogger(__name__)

# 技术员预录的人声提示音
PROMPT_DIR = "/opt/hud_navi/data/voice_prompts"
WAV_MAP = {
    "去公司": "cmd_qugongsi.wav",
    "回家": "cmd_huijia.wav",
    "开始导航": "cmd_kaishi.wav",
    "取消": "cmd_quxiao.wav",
    "到达": "all_done.wav",
    "声纹通过": "vp_pass.wav",
    "声纹失败": "vp_fail.wav",
    "识别成功": "cmd_ok.wav",
    "未识别": "cmd_fail.wav",
    "已完成": "done.wav",
    "开始说话": "start_speak.wav",
    "距离": "cmd_duoyuan.wav",  # "还有多远"
}

# 尝试导入 gTTS
try:
    from gtts import gTTS
    import pygame
    _gtts_ok = True
    try: pygame.mixer.init(); logger.info("TTS: pygame 就绪")
    except: pass
except ImportError:
    _gtts_ok = False

class VoiceAlert:
    def __init__(self):
        self._running = False
        self._thread = None
        self._queue = queue.Queue(maxsize=10)
        self._last = ""
        self._last_time = 0
        self._cache_dir = "/tmp/tts_cache"
        os.makedirs(self._cache_dir, exist_ok=True)

    def init_tts(self) -> bool:
        if _gtts_ok:
            logger.info("TTS: gTTS 真人声 (在线)")
        elif os.path.exists(PROMPT_DIR):
            logger.info("TTS: 预录 WAV 人声 (离线)")
        else:
            logger.info("TTS: ALSA 蜂鸣 (降级)")
        return True

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="Voice")
        self._thread.start()

    def stop(self):
        self._running = False
        try: self._queue.put_nowait(None)
        except: pass
        if self._thread: self._thread.join(timeout=3)

    def speak(self, text: str, force: bool = False):
        if not force and text == self._last:
            if time.time() - self._last_time < 5.0: return
        try:
            self._queue.put_nowait(text)
            self._last = text
            self._last_time = time.time()
        except queue.Full: pass

    def _loop(self):
        from voice_command import audio_lock  # 共享Codec互斥锁
        while self._running:
            try:
                text = self._queue.get(timeout=1)
                if text is None: break
                logger.info(f"语音: {text}")
                with audio_lock:  # 播放时锁Codec, 防止录音冲突
                    if _gtts_ok and self._try_gtts(text): pass
                    elif self._try_speak_helper(text): pass
                    elif self._try_wav(text): pass
                    else: self._try_beep(text)
            except queue.Empty: continue

    # ---- gTTS 在线真人声 ----
    def _try_gtts(self, text: str) -> bool:
        try:
            cache = os.path.join(self._cache_dir, text.replace("/","_")[:50] + ".mp3")
            if not os.path.exists(cache) or os.path.getsize(cache) == 0:
                tts = gTTS(text=text, lang="zh-cn", slow=False)
                tts.save(cache)
                if os.path.getsize(cache) == 0:
                    return False  # 网络不通，让WAV降级
            subprocess.run(["mpg123", "-q", "-a", "plughw:2,0", cache], timeout=10)
            return True
        except Exception as e:
            logger.debug(f"gTTS失败: {e}")
            return False

    # ---- Edge-TTS (免费在线) ----
    def _try_speak_helper(self, text: str) -> bool:
        try:
            from speak_helper import speak as edge_speak
            cache = edge_speak(text)  # 返回MP3路径
            if cache and os.path.exists(cache):
                subprocess.run(["mpg123", "-q", "-a", "plughw:2,0", cache], timeout=10)
                return True
        except Exception as e:
            logger.debug(f"Edge-TTS失败: {e}")
        return False

    # ---- 技术员预录 WAV 人声 ----
    def _try_wav(self, text: str) -> bool:
        fname = None
        for key, val in WAV_MAP.items():
            if key in text:
                fname = val; break
        if not fname:
            # 转向匹配
            if "左转" in text: fname = "cmd_quxiao.wav"  # 暂无专用WAV, 用通用提示
            elif "右转" in text: fname = "cmd_quxiao.wav"
            elif "掉头" in text: fname = "cmd_quxiao.wav"
            elif "直行" in text: fname = "cmd_ok.wav"
            elif "到达" in text: fname = "all_done.wav"
            elif "导航" in text or "开始" in text: fname = "cmd_kaishi.wav"
            else: fname = None

        if fname:
            path = os.path.join(PROMPT_DIR, fname)
            if os.path.exists(path):
                try:
                    subprocess.run(["aplay", "-q", "-D", "plughw:2,0", path], timeout=5)
                    return True
                except Exception:
                    pass
        # 距离播报用 beep (数字播报)
        if "米" in text or "公里" in text or "km" in text.lower():
            return False  # 让 beep 处理
        return False

    # ---- ALSA 蜂鸣降级 ----
    def _try_beep(self, text: str) -> bool:
        import math, struct
        beeps, freq = 1, 800
        if "左转" in text: beeps, freq = 2, 600
        elif "右转" in text: beeps, freq = 2, 1000
        elif "掉头" in text: beeps, freq = 3, 500
        elif "到达" in text: beeps, freq = 4, 1200

        for _ in range(beeps):
            try:
                tone = self._gen_tone(freq, 0.15, 16000)
                subprocess.run(["aplay", "-q", "-D", "plughw:2,0", "-t", "raw", "-f", "S16_LE",
                    "-r", "16000", "-c", "1", "-d", "0.15"], input=tone, timeout=1)
            except: pass
        return True

    def _gen_tone(self, freq, dur, rate):
        import math, struct
        d = bytearray()
        for i in range(int(rate*dur)):
            v = int(8000 * math.sin(2*math.pi*freq*i/rate))
            d.extend(struct.pack("<hh", v, v))
        return bytes(d)
