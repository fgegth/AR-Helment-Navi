"""
声纹锁模块 — 超轻版 (无 numpy)
用 Goertzel 算法提取频带能量 → cosine 比对
"""
import json, math, os, struct, subprocess, wave

FEAT_FILE = "/opt/hud_navi/data/voiceprint.json"
AUDIO_DEVICE = "plughw:2,0"  # USB麦克风，自动重采样
SAMPLE_RATE = 16000
CHANNELS = 1  # USB麦克风单声道

# 16 个关键频率 (覆盖人声 80Hz~4000Hz 的对数分布)
BANDS = [80, 120, 180, 250, 330, 420, 520, 630,
         760, 900, 1050, 1220, 1400, 1600, 1820, 2060,
         2320, 2600, 2900, 3220, 3560, 3920, 4300, 4700,
         350, 580, 950, 1550, 2100, 2800, 3400, 4100]

def record_audio(filename: str, duration: int = 3):
    subprocess.run(["arecord", "-D", AUDIO_DEVICE, "-d", str(duration),
        "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", str(CHANNELS),
        filename], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def read_wav(filename: str) -> list:
    wf = wave.open(filename, "rb")
    n = wf.getnframes()
    ch = wf.getnchannels()
    raw = wf.readframes(n)
    wf.close()
    samples = []
    step = 2 * ch  # 单声道=2, 双声道=4
    for i in range(0, len(raw), step):
        if i + 1 < len(raw):
            val = struct.unpack("<h", raw[i:i+2])[0]
            samples.append(val / 32768.0)
    return samples

def goertzel(samples: list, freq: float, rate: int = SAMPLE_RATE) -> float:
    """Goertzel 算法 — 计算单个频率的能量 (O(n), 比 FFT 快得多)"""
    n = len(samples)
    k = int(0.5 + n * freq / rate)
    omega = 2.0 * math.pi * k / n
    cos_w = math.cos(omega)
    coeff = 2.0 * cos_w
    s0, s1, s2 = 0.0, 0.0, 0.0
    for sample in samples:
        s0 = sample + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return math.sqrt(s2 * s2 + s1 * s1 - coeff * s1 * s2) / n

def extract_features(samples: list) -> list:
    """16 维频带能量向量 (约 0.1 秒完成)"""
    features = []
    for freq in BANDS:
        energy = goertzel(samples, freq)
        features.append(energy)
    # 归一化
    total = sum(features) + 1e-10
    return [f / total for f in features]

def cosine_sim(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-10)

# ===================== API =====================

def _rms(samples):
    if not samples: return 0
    return math.sqrt(sum(s*s for s in samples)/len(samples))

def enroll(name: str = "owner", duration: int = 3) -> bool:
    from voice_command import audio_lock  # 避免循环导入
    import time as _t
    print(f"声纹注册: 请说一句话 ({duration}秒)...")
    with audio_lock:
        record_audio("/tmp/enroll.wav", duration)
    samples = read_wav("/tmp/enroll.wav")
    if len(samples) < 1000:
        print("录音太短"); return False
    if _rms(samples) < 0.003:
        print("未检测到人声, 请重新录入"); return False
    feats = extract_features(samples)
    os.makedirs(os.path.dirname(FEAT_FILE), exist_ok=True)
    data = {}
    if os.path.exists(FEAT_FILE):
        with open(FEAT_FILE) as f: data = json.load(f)
    data[name] = feats   # 声纹验证用 "owner"
    data["_count"] = int(data.get("_count", 0)) + 1  # APK 用递增计数
    # 原子写入：先写临时文件再 rename，避免 HTTP 读到半截文件
    tmp = FEAT_FILE + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f)
    os.replace(tmp, FEAT_FILE)
    print(f"✅ 声纹已注册: {name} (第{data['_count']}次)")
    return True

def verify(name: str = "owner", threshold: float = 0.92, duration: int = 2) -> bool:
    if not os.path.exists(FEAT_FILE):
        print("无声纹数据")
        return False
    with open(FEAT_FILE) as f: data = json.load(f)
    if name not in data:
        print(f"未找到: {name}")
        return False
    print(f"声纹验证: 请说话 ({duration}秒)...")
    record_audio("/tmp/verify.wav", duration)
    samples = read_wav("/tmp/verify.wav")
    if _rms(samples) < 0.003:
        print("未检测到人声"); return False
    feats = extract_features(samples)
    sim = cosine_sim(feats, data[name])
    print(f"相似度: {sim:.3f} (阈值: {threshold})")
    return sim >= threshold

def is_owner() -> bool:
    return verify("owner", 0.90, 2)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "enroll":
        enroll()
    else:
        ok = is_owner()
        print("✅ 声纹通过" if ok else "❌ 声纹拒绝")
