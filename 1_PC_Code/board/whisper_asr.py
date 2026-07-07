"""
Whisper 语音识别集成 — 替代模板匹配
需要 RKNN 模型文件在 /data/whisper/model/
板子插回后: python whisper_asr.py test → 跑中文测试音频
"""
import subprocess, os, json, re

WHISPER_DIR = "/data/whisper"
ENCODER = f"{WHISPER_DIR}/model/whisper_encoder_base_20s.rknn"
DECODER = f"{WHISPER_DIR}/model/whisper_decoder_base_20s.rknn"
BINARY = f"{WHISPER_DIR}/rknn_whisper_demo"
LIB_PATH = f"{WHISPER_DIR}/lib"

# zhconv 繁→简转换 (导入时检查, 避免每次调用重复 import)
try:
    import zhconv as _zhconv
    _zhconv_ok = True
except ImportError:
    _zhconv = None
    _zhconv_ok = False

# 可识别的命令关键词 (Whisper 输出模糊匹配, intent_engine 优先)
COMMANDS = {
    "去公司": ["去公司", "公司"],
    "回家": ["回家", "回去"],
    "取消导航": ["取消", "取消导航", "停止导航", "关闭导航"],
    "还有多远": ["还有多远", "多远", "距离"],
    "开始导航": ["开始导航", "开始", "导航", "启动导航"],
    "继续导航": ["继续", "继续走"],
    "切换离线": ["离线", "切换离线", "离线模式"],
    "减速": ["减速", "慢点", "太快了"],
}

def _check_npu() -> bool:
    """检查NPU是否可用: 仅做文件存在性检查 (不启动推理进程)"""
    if not os.path.exists(BINARY) or not os.access(BINARY, os.X_OK):
        return False
    if not os.path.exists(ENCODER):
        return False
    if not os.path.exists(DECODER):
        return False
    if not os.path.exists(LIB_PATH):
        return False
    return True

def audio_to_text(wav_path: str, language: str = "zh") -> str:
    """
    调用 Whisper NPU 推理，WAV → 文本
    返回: 识别的中文文本，失败返回 ""
    """
    try:
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
            return ""
        result = subprocess.run(
            [BINARY, ENCODER, DECODER, language, wav_path],
            cwd=WHISPER_DIR,
            env={**os.environ, "LD_LIBRARY_PATH": LIB_PATH},
            capture_output=True, timeout=15
        )
        output = result.stdout.decode("utf-8", errors="ignore")
        match = re.search(r"Whisper output:\s*(.+)", output)
        if match:
            text = match.group(1).strip()
            if not text or text.startswith("Real Time Factor"):
                return ""  # 静音/噪声, 无有效文字
        else:
            return ""  # 没找到识别输出
        # 繁→简转换
        if _zhconv_ok:
            text = _zhconv.convert(text, "zh-cn")
        return text
    except subprocess.TimeoutExpired:
        print("Whisper: timeout")
        return ""
    except FileNotFoundError:
        print("Whisper: binary not found")
        return ""
    except OSError:
        print("Whisper: NPU driver error")
        return ""
    except Exception as e:
        print(f"Whisper: {e}")
        return ""

def match_command(text: str) -> str:
    """
    文本 → 命令匹配 (使用 intent_engine)
    """
    try:
        from intent_engine import extract_intent
        result = extract_intent(text)
        if result["intent"] != "unknown":
            return result["intent"]
    except ImportError:
        pass
    # 降级: 旧版关键词匹配
    text = text.lower().replace(" ", "")
    best_cmd, best_len = "", 0
    for cmd, keywords in COMMANDS.items():
        for kw in keywords:
            if kw in text and len(kw) > best_len:
                best_cmd, best_len = cmd, len(kw)
    return best_cmd

def recognize_from_wav(wav_path: str) -> str:
    """WAV 文件 → 语音识别 → 意图提取"""
    text = audio_to_text(wav_path)
    if not text:
        return ""
    print(f"Whisper: {text}")
    cmd = match_command(text)
    print(f"Command: {cmd or '(未匹配)'}")
    return cmd

def recognize_with_intent(wav_path: str) -> dict:
    """WAV → 意图 (完整信息, 含目标地名和坐标)"""
    text = audio_to_text(wav_path)
    if not text:
        return {"intent": "unknown", "target": "", "lat": None, "lon": None}
    print(f"Whisper: {text}")
    try:
        from intent_engine import extract_intent
        result = extract_intent(text)
        print(f"Intent: {result['intent']}, target: {result['target']}")
        return result
    except ImportError:
        cmd = match_command(text)
        return {"intent": cmd if cmd else "unknown", "target": "", "lat": None, "lon": None}

# ==================== 测试 ====================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = f"{WHISPER_DIR}/model/test_zh.wav"

    print(f"Testing: {path}")
    text = audio_to_text(path)
    print(f"Whisper output: {text}")
    cmd = match_command(text)
    print(f"Matched command: {cmd or 'NONE'}")
