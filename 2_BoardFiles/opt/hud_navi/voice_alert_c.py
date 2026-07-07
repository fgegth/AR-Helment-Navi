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

# 尝试导入 gTTS（不依赖 pygame，mpg123 直接播MP3）
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
        self._last = ""
        self._last_time = 0
        self._cache_dir = "/tmp/tts_cache"
        self._cache_max_files = 200   # 最多200个缓存文件 (~10-20MB)
        self._edge_tts = None  # Edge-TTS 单例 (延迟初始化)
        os.makedirs(self._cache_dir, exist_ok=True)
        self._cleanup_cache()

    def _get_edge_tts(self):
        """获取 Edge-TTS 单例 (延迟初始化, 避免未安装时崩溃)"""
        if self._edge_tts is None:
            try:
                from tts_edge import EdgeTTS
                self._edge_tts = EdgeTTS()
            except Exception:
                self._edge_tts = False  # 标记不可用
        return self._edge_tts if self._edge_tts is not False else None

    def _cleanup_cache(self):
        """清理过期TTS缓存, 保留最近200个文件"""
        try:
            files = sorted(
                [os.path.join(self._cache_dir, f) for f in os.listdir(self._cache_dir)],
                key=os.path.getmtime, reverse=True)
            for old in files[self._cache_max_files:]:
                try: os.remove(old)
                except OSError: pass
        except Exception:
            pass

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
        # 后台预缓存常用语音（使用单例Edge-TTS）
        threading.Thread(target=self._pre_cache, daemon=True).start()

    def _pre_cache(self):
        """预缓存: 如果是 Edge-TTS, 导航中生成会有 1-2s 网络延迟"""
        phrases = ["当前没有导航", "已到达目的地", "开始导航", "导航已取消"]
        tts = self._get_edge_tts()
        if tts is None:
            return
        for text in phrases:
            try:
                cache = os.path.join(self._cache_dir, "edge_" + text.replace("/","_")[:50] + ".mp3")
                if not os.path.exists(cache) or os.path.getsize(cache) == 0:
                    tts._generate(text, cache)
                    logger.info(f"TTS预缓存: {text}")
            except Exception:
                pass

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
        except queue.Full:
            if force:
                # 紧急消息排队满时, 尝试等待0.5秒插入
                try:
                    self._queue.put(text, timeout=0.5)
                    self._last = text
                    self._last_time = time.time()
                    return
                except queue.Full:
                    pass
            logger.warning(f"语音队列满, 丢弃: {text}")

    def _loop(self):
        from voice_command import audio_lock  # 共享Codec互斥锁
        while self._running:
            try:
                text = self._queue.get(timeout=1)
                if text is None: break
                logger.info(f"语音: {text}")
                with audio_lock:  # 播放时锁Codec, 防止录音冲突
                    # 1. Piper TTS 离线真人声 (优先, 无需网络, ARM64原生)
                    if self._try_piper(text):
                        pass
                    # 2. Edge-TTS 在线免费 (国内可用, 首次生成有1-2s延迟)
                    elif self._try_speak_helper(text):
                        pass
                    # 3. gTTS 在线谷歌 (需翻墙, 质量最好)
                    elif self._try_gtts(text):
                        pass
                    # 4. 预录WAV (离线备用, 仅12个固定短语)
                    elif self._try_wav(text):
                        pass
                    # 5. 蜂鸣兜底 (永远可用, 0.15s短促提示音)
                    else:
                        self._try_beep(text)
            except queue.Empty: continue
            except Exception as e:
                logger.error(f"语音播报异常: {e}", exc_info=True)
                # 单条语音失败不终止线程, 继续处理下一条

    # ---- Piper TTS 离线真人声 ----
    def _try_piper(self, text: str) -> bool:
        """Piper TTS: 纯离线, ARM64原生, 中文真人声, 无需网络"""
        try:
            from hud_tts import speak_offline
            return speak_offline(text)
        except ImportError:
            return False
        except Exception as e:
            logger.debug(f"Piper TTS 失败: {e}")
            return False

    # ---- gTTS 在线真人声 ----
    def _try_gtts(self, text: str) -> bool:
        try:
            cache = os.path.join(self._cache_dir, "gtts_" + text.replace("/","_")[:50] + ".mp3")
            if not os.path.exists(cache) or os.path.getsize(cache) == 0:
                if os.path.exists(cache): os.remove(cache)  # 删除0字节缓存
                tts = gTTS(text=text, lang="zh-cn", slow=False)
                tts.save(cache)
                if os.path.getsize(cache) == 0:
                    return False  # 网络不通
            subprocess.run(["mpg123", "-q", "-a", "plughw:2,0", cache], timeout=10)
            return True
        except Exception as e:
            logger.debug(f"gTTS失败: {e}")
            return False

    # ---- Edge-TTS (免费在线，国内可用) ----
    def _try_speak_helper(self, text: str) -> bool:
        """使用单例 Edge-TTS: 首次调用初始化, 后续复用 (避免重复创建实例+网络检查)"""
        tts = self._get_edge_tts()
        if tts is None or not tts.is_available():
            return False
        try:
            cache = tts.save_to_file(text)
            if cache and os.path.exists(cache) and os.path.getsize(cache) > 0:
                dev = "plughw:2,0"
                try:
                    from audio_manager import get_output_device
                    dev = get_output_device()
                except Exception:
                    pass
                subprocess.run(["mpg123", "-q", "-a", dev, cache], timeout=10)
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
            # 转向匹配 (有WAV就用, 没有也能走beep降级)
            if "左转" in text: fname = "cmd_quxiao.wav"
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
        # 距离播报用 beep
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
