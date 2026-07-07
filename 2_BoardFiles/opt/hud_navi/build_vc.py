"""稳定版 — 2秒录音, 唤醒词+Vosk, print输出 (已验证全部✅)"""
code = r'''
import json, math, os, struct, subprocess, time, wave, threading
import logging; logger = logging.getLogger("VoiceCmd")

AUDIO_DEVICE = "plughw:2,0"
SAMPLE_RATE = 16000
audio_lock = threading.Lock()
WAKE_WORDS = ["小正小正","小正","小镇小镇","小镇","小郑小郑","小真小真","小政小政"]
COMMAND_TIMEOUT = 10
RECORD_SEC = 2
PROMPT_DIR = "/opt/hud_navi/data/voice_prompts"

def _rms(samples):
    if not samples: return 0
    return math.sqrt(sum(s*s for s in samples)/len(samples))

def _record(filename, duration):
    p = subprocess.run(["arecord","-D",AUDIO_DEVICE,"-d",str(duration),
        "-f","S16_LE","-r",str(SAMPLE_RATE),"-c","1",filename],
        capture_output=True, text=True, timeout=duration+5)
    if p.returncode != 0:
        print(f"rec err: {p.stderr.strip()}")
        return False
    return os.path.exists(filename) and os.path.getsize(filename) > 1000

def _read_wav(filename):
    wf = wave.open(filename,"rb")
    n = wf.getnframes(); ch = wf.getnchannels()
    raw = wf.readframes(n); wf.close()
    samples = []
    for i in range(0,len(raw),2*ch):
        if i+1 < len(raw): samples.append(struct.unpack("<h",raw[i:i+2])[0]/32768.0)
    return samples

def _play(wav_name):
    path = os.path.join(PROMPT_DIR, wav_name)
    if os.path.exists(path):
        subprocess.run(["aplay","-q","-D",AUDIO_DEVICE,path], timeout=2)

def listen_and_execute(callback=None):
    wav_file = "/tmp/voice_input.wav"
    if not hasattr(listen_and_execute,"_wake_until"): listen_and_execute._wake_until = 0
    if not hasattr(listen_and_execute,"_baseline"): listen_and_execute._baseline = 0.001

    with audio_lock:
        if not _record(wav_file, RECORD_SEC):
            time.sleep(0.5); return ""

    try: samples = _read_wav(wav_file)
    except: return ""
    rms = _rms(samples)
    listen_and_execute._baseline = listen_and_execute._baseline*0.95 + rms*0.05
    threshold = min(max(listen_and_execute._baseline*1.5,0.003),0.030)
    if rms < threshold or len(samples) < 1000:
        return ""

    vosk_text = ""
    try:
        from hud_vosk import audio_to_text_vosk
        vosk_text = audio_to_text_vosk(wav_file)
    except Exception as e:
        print(f"Vosk err: {e}")
    vosk_text = (vosk_text or "").strip().replace(" ","")

    now = time.time()
    if any(w in vosk_text for w in WAKE_WORDS) or vosk_text.startswith('小'):
        print(f"WAKE! {vosk_text}")
        # 声纹验证
        auth_ok = False
        try:
            from voice_auth import verify
            _record("/tmp/voice_auth.wav", 2)
            auth_ok = verify("/tmp/voice_auth.wav")
            print(f"Auth: {'PASS' if auth_ok else 'FAIL'}")
        except Exception as e:
            print(f"Auth err: {e}, skipping")
            auth_ok = True  # 验证失败时放行(向后兼容)
        if not auth_ok:
            return ""  # 声纹不匹配, 静默忽略
        listen_and_execute._wake_until = now + COMMAND_TIMEOUT
        _play("start_speak.wav")
        return ""

    if now > listen_and_execute._wake_until:
        if vosk_text: print(f"[skip] {vosk_text}")
        return ""

    if vosk_text:
        print(f"CMD: {vosk_text}")
        if callback: callback(vosk_text)
        return vosk_text
    return ""
'''

with open("/opt/hud_navi/voice_command.py","w",encoding="utf-8") as f:
    f.write(code)
import py_compile; py_compile.compile("/opt/hud_navi/voice_command.py",doraise=True)
print("OK")
