"""板子端修改 voice_command.py — Vosk + 唤醒词 + aplay修复"""
import re

path = "/opt/hud_navi/voice_command.py"

# 1. 二进制修复: 清除所有 BOM 损坏字符
with open(path, "rb") as f:
    raw = f.read()
raw = raw.replace(b'\xEE\x86\x8D', b' ')
raw = raw.replace(b'\xE2\x82\xAC', b' ')

text = raw.decode("utf-8", errors="replace")

# 2. aplay: 0.08→1
text = text.replace("0.08", "1")

# 3. RECORD_SEC: 10→3 (交付包是10)
text = text.replace("RECORD_SEC = 10", "RECORD_SEC = 3")

# 4. Whisper → Vosk
text = text.replace(
    "from whisper_asr import audio_to_text",
    "from hud_vosk import audio_to_text_vosk"
)
text = text.replace(
    'whisper_text = audio_to_text(wav_file)',
    'vosk_text = audio_to_text_vosk(wav_file)'
)
text = text.replace(
    'whisper_text = (whisper_text or "").strip()',
    'vosk_text = (vosk_text or "").strip().replace(" ", "")'
)
text = text.replace(
    'print(f"  Whisper: {whisper_text}")',
    'print(f"  Vosk: {vosk_text}")'
)

# 5. 在 listen_and_execute 函数体内加唤醒词逻辑
# 在 "wav_file = ..." 行后面插入 WAKE_WORDS
old_line = 'wav_file = "/tmp/voice_input.wav"\n    RECORD_SEC = 3'
new_line = '''wav_file = "/tmp/voice_input.wav"
    RECORD_SEC = 3
    WAKE_WORDS = ["小正小正", "小正", "小镇小镇", "小镇", "小郑小郑", "小真小真", "小政小政"]
    COMMAND_TIMEOUT = 10
    if not hasattr(listen_and_execute, "_wake_until"):
        listen_and_execute._wake_until = 0'''
text = text.replace(old_line, new_line)

# 6. 识别成功后加唤醒检测(替换原始决策逻辑)
old = '''    if whisper_text:
        best_cmd = whisper_text
        print(f"  Whisper: {whisper_text}")'''
new = '''    now = time.time()
    is_wake = any(w in vosk_text for w in WAKE_WORDS)
    if is_wake:
        listen_and_execute._wake_until = now + COMMAND_TIMEOUT
        print(f"🔔 唤醒!")
        try:
            import subprocess as _sp
            _sp.run(["aplay","-q","-D","plughw:2,0","/opt/hud_navi/data/voice_prompts/start_speak.wav"],timeout=2)
        except: pass
        return ""
    if now > listen_and_execute._wake_until:
        if vosk_text: print(f"  [忽略] {vosk_text}")
        return ""
    if vosk_text:
        best_cmd = vosk_text
        print(f"  Vosk: {vosk_text}")'''
text = text.replace(old, new)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("✅ 语法OK")
except Exception as e:
    print(f"❌ {e}")

# 验证
for line in text.split("\n"):
    if "WAKE_WORDS" in line:
        print(f"唤醒词: {line.strip()}")
    if "hud_vosk" in line:
        print(f"Vosk导入: {line.strip()}")
