
import json, math, os, struct, subprocess, time, wave, threading
import logging; logger = logging.getLogger("VoiceCmd")

AUDIO_DEVICE = "plughw:2,0"
SAMPLE_RATE = 16000
audio_lock = threading.Lock()
WAKE_WORDS = ["小正小正","小正","小镇小镇","小镇","小郑小郑","小真小真","小政小政"]
COMMAND_TIMEOUT = 10
RECORD_SEC = 3
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
    # 首次调用时重置唤醒窗口(防旧时间戳残留)
    if not hasattr(listen_and_execute,"_init"):
        listen_and_execute._init = True
        listen_and_execute._wake_until = 0
        listen_and_execute._wake_count = 0

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

    # 唤醒词检测
    if any(w in vosk_text for w in WAKE_WORDS) or vosk_text.startswith('小'):
        # 声纹验证(同一段录音)
        if os.path.exists("/opt/hud_navi/data/voiceprint.json"):
            try:
                from voice_auth import extract_features, cosine_sim
                import json as _json
                feats = extract_features(samples)
                with open("/opt/hud_navi/data/voiceprint.json") as f:
                    vp = _json.load(f)
                if "owner" in vp:
                    sim = cosine_sim(feats, vp["owner"])
                    print(f"声纹: {sim:.3f}")
                    if sim < 0.50:
                        return ""
            except Exception as e:
                print(f"声纹跳过: {e}")
        listen_and_execute._wake_count = COMMAND_TIMEOUT  # 10次录音机会
        print(f"WAKE! {vosk_text}")
        _play("start_speak.wav")
        return ""

    # 唤醒窗口内: 倒数计数, 不依赖系统时钟
    if listen_and_execute._wake_count > 0:
        listen_and_execute._wake_count -= 1
        if vosk_text:
            print(f"CMD: {vosk_text}")
            if callback: callback(vosk_text)
            listen_and_execute._wake_count = 0  # 执行后关闭窗口
            return vosk_text
    else:
        if vosk_text: print(f"[skip] {vosk_text}")
    return ""

