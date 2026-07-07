"""
HUD Vosk 离线语音识别 — 替代 Whisper NPU
纯CPU、50MB模型、百毫秒延迟、实时流式识别

用法:
  from hud_vosk import VoskASR
  asr = VoskASR()
  text = asr.recognize_wav("/tmp/voice.wav")
"""
import json, logging, os

logger = logging.getLogger(__name__)

MODEL_PATH = "/opt/hud_navi/data/vosk_model/vosk-model-small-cn-0.22"

_vosk_model = None

def _get_model():
    global _vosk_model
    if _vosk_model is None:
        from vosk import Model
        _vosk_model = Model(MODEL_PATH)
        logger.info("Vosk 模型加载完成")
    return _vosk_model


class VoskASR:
    def __init__(self):
        self._model = _get_model()

    def recognize_wav(self, wav_path: str) -> str:
        """WAV文件 → 中文文本 (16000Hz, 单声道, 16bit)"""
        try:
            from vosk import KaldiRecognizer
            import wave
            wf = wave.open(wav_path, "rb")
            if wf.getframerate() != 16000:
                wf.close()
                return ""
            rec = KaldiRecognizer(self._model, 16000)
            rec.SetWords(True)
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                rec.AcceptWaveform(data)
            wf.close()
            result = json.loads(rec.FinalResult())
            return result.get("text", "").strip()
        except Exception as e:
            logger.error(f"Vosk识别失败: {e}")
            return ""


_asr = None

def get_asr() -> VoskASR:
    global _asr
    if _asr is None:
        _asr = VoskASR()
    return _asr

def audio_to_text_vosk(wav_path: str) -> str:
    """便捷函数: 与whisper_asr.audio_to_text同接口"""
    return get_asr().recognize_wav(wav_path)
