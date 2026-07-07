"""
语音命令识别 — Vosk 离线识别 (纯CPU, 50MB模型, 识别率最高)
命令集: 自然语言自由输入 → intent_engine 提取意图
触发方式: 持续监听 → 人声检测 → 声纹验证 → Vosk 识别 → 意图解析
"""
import json, math, os, struct, subprocess, time, wave, sys, threading
from voice_auth import record_audio, read_wav, extract_features, cosine_sim

CMD_FILE = "/opt/hud_navi/data/commands.json"
AUDIO_DEVICE = "plughw:2,0"
SAMPLE_RATE = 16000
audio_lock = threading.Lock()

def _rms(samples: list) -> float:
    if not samples: return 0
    return math.sqrt(sum(s*s for s in samples) / len(samples))

def _play_ack_beep():
    """播放确认音: 短促高频beep告诉用户"听到了, 正在处理" (非阻塞)"""
    import struct as _st
    rate, freq, dur = 16000, 1200, 0.08
    tone = bytearray()
    for i in range(int(rate * dur)):
        v = int(4000 * math.sin(2 * math.pi * freq * i / rate))
        tone.extend(_st.pack("<hh", v, v))
    try:
        subprocess.run(["aplay", "-q", "-D", AUDIO_DEVICE, "-t", "raw",
            "-f", "S16_LE", "-r", str(rate), "-c", "1", "-d", str(dur)],
            input=bytes(tone), timeout=1)
    except Exception:
        pass

def wait_for_voice() -> bool:
    """阻塞等待直到检测到人声"""
    with audio_lock:
        record_audio("/tmp/vad_baseline.wav", 1)
    baseline = _rms(read_wav("/tmp/vad_baseline.wav"))
    threshold = min(max(baseline * 1.5, 0.003), 0.030)
    print(f"  VAD 基线: {baseline:.4f}, 阈值: {threshold:.4f}")
    while True:
        with audio_lock:
            record_audio("/tmp/vad_check.wav", 1)
            energy = _rms(read_wav("/tmp/vad_check.wav"))
        if energy > threshold:
            return True
        time.sleep(0.3)

# 预置命令模板 (保留兼容 HTTP /voice/cmd_enroll)
DEFAULT_COMMANDS = ["去公司", "回家", "取消导航", "还有多远", "开始导航"]

def enroll_command(name: str):
    print(f"录制命令: {name} (请说「{name}」)")
    with audio_lock:
        record_audio(f"/tmp/cmd_{name}.wav", 2)
    samples = read_wav(f"/tmp/cmd_{name}.wav")
    feats = extract_features(samples)
    os.makedirs(os.path.dirname(CMD_FILE), exist_ok=True)
    data = {}
    if os.path.exists(CMD_FILE):
        with open(CMD_FILE) as f: data = json.load(f)
    data[name] = feats
    tmp = CMD_FILE + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f)
    os.replace(tmp, CMD_FILE)
    print(f"  ✅ 已录制: {name}")

def enroll_all():
    print("=== 语音命令录制 (请逐个说以下命令) ===")
    for cmd in DEFAULT_COMMANDS:
        enroll_command(cmd)
    # 递增计数（APK 轮询用）+ 原子写入防冲突
    if os.path.exists(CMD_FILE):
        with open(CMD_FILE) as f: data = json.load(f)
        data["_count"] = int(data.get("_count", 0)) + 1
        tmp = CMD_FILE + ".tmp"
        with open(tmp, "w") as f: json.dump(data, f)
        os.replace(tmp, CMD_FILE)
    print("=== 全部录制完成 ===")

def recognize(timeout: float = 0.75) -> str:
    if not os.path.exists(CMD_FILE):
        print("无命令模板，请先运行 enroll_all()")
        return ""
    try:
        with open(CMD_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError):
        print("命令模板损坏, 请重新录入")
        return ""
    with audio_lock:
        record_audio("/tmp/cmd_unknown.wav", 2)
    samples = read_wav("/tmp/cmd_unknown.wav")
    feats = extract_features(samples)
    best_cmd, best_sim = "", 0
    for cmd, template in data.items():
        if cmd.startswith("_"): continue  # 跳过 _count 等元数据
        sim = cosine_sim(feats, template)
        if sim > best_sim: best_sim, best_cmd = sim, cmd
    print(f"  识别: {best_cmd} (相似度 {best_sim:.3f})")
    return best_cmd if best_sim >= timeout else ""

def listen_loop(callback=None):
    """持续监听循环 (永不退出, 后台线程)"""
    print("语音监听已启动 (检测人声自动触发)...")
    while True:
        try:
            if not wait_for_voice():
                time.sleep(0.5); continue
            print("🎤 检测到人声，正在识别...")
            record_audio("/tmp/voice_input.wav", 2)
            # 声纹验证
            from voice_auth import verify
            if not verify("owner", 0.70, 2):
                print("❌ 声纹不匹配"); continue
            # Vosk 离线识别
            try:
                from hud_vosk import audio_to_text_vosk
                text = audio_to_text_vosk("/tmp/voice_input.wav")
            except Exception as e:
                print(f"Vosk 识别失败: {e}"); continue
            text = (text or "").strip().replace(" ", "")
            if text:
                print(f"  Vosk: {text}")
                if callback: callback(text)
            else:
                print("❓ 未识别")
        except Exception as e:
            print(f"语音监听异常: {e}"); time.sleep(1)

def listen_and_execute(callback=None):
    """
    单次识别流程 — Vosk 离线识别 + intent_engine 意图解析
    录音5秒 → 人声检测 → 声纹验证 → Vosk → 回调
    """
    wav_file = "/tmp/voice_input.wav"
    RECORD_SEC = 5
    with audio_lock:
        record_audio(wav_file, RECORD_SEC)
    samples = read_wav(wav_file)
    rms = _rms(samples)
    # 动态阈值：首次录音作为环境基线，后续 1.5倍基线 触发
    if not hasattr(listen_and_execute, "_baseline"):
        listen_and_execute._baseline = max(rms, 0.001)
        return ""  # 首帧用于校准
    threshold = min(max(listen_and_execute._baseline * 1.5, 0.003), 0.030)
    # 缓慢更新基线（适应环境噪音变化）
    listen_and_execute._baseline = listen_and_execute._baseline * 0.95 + rms * 0.05
    if rms < threshold or len(samples) < 1000:
        return ""  # 低于阈值，跳过
    # 立即反馈: 轻触音告诉用户"听到了, 正在处理"
    threading.Thread(target=lambda: _play_ack_beep(), daemon=True).start()
    print(f"🎤 Vosk 识别中... (RMS={rms:.4f}, 阈值={threshold:.4f}, 录音{RECORD_SEC}s)")

    # 声纹验证 (可选, 有数据才验证)
    if os.path.exists(CMD_FILE):
        # 先用 CMD_FILE 的同目录找 FEAT_FILE
        from voice_auth import FEAT_FILE
        if os.path.exists(FEAT_FILE):
            feats = extract_features(samples)
            try:
                with open(FEAT_FILE) as f:
                    vp_data = json.load(f)
                if "owner" in vp_data:
                    vp_sim = cosine_sim(feats, vp_data["owner"])
                    if vp_sim < 0.50:
                        print(f"❌ 声纹不匹配 ({vp_sim:.3f})"); return ""
            except (json.JSONDecodeError, ValueError):
                pass  # 声纹数据损坏, 跳过验证

    # Vosk 离线语音识别 (纯CPU, 50MB模型, ~100ms延迟, 识别率最高)
    try:
        from hud_vosk import audio_to_text_vosk
        text = audio_to_text_vosk(wav_file)
    except ImportError:
        print("Vosk 未安装, 请部署模型到 /opt/hud_navi/data/vosk_model/")
        return ""
    except Exception as e:
        print(f"Vosk 识别异常: {e}")
        return ""
    text = (text or "").strip().replace(" ", "")
    if text:
        print(f"  Vosk: {text}")
        if callback: callback(text)
        return text
    else:
        print("❓ 未识别"); return ""

def calibrate():
    if not os.path.exists(CMD_FILE):
        print("请先执行 enroll 录制命令"); return
    with open(CMD_FILE) as f: data = json.load(f)
    print("=== 命令校准 (逐个说命令，每个 2 秒) ===")
    results = {}
    for cmd in data:
        input(f"准备好说「{cmd}」，按回车开始...")
        record_audio("/tmp/cal.wav", 2)
        feats = extract_features(read_wav("/tmp/cal.wav"))
        scores = {}
        for tcmd, tpl in data.items():
            scores[tcmd] = cosine_sim(feats, tpl)
        results[cmd] = scores
        best = max(scores, key=scores.get)
        icon = "✅" if best == cmd else "❌"
        print(f"  {icon} 说的是「{cmd}」→ 识别为「{best}」")
        for c, s in sorted(scores.items(), key=lambda x: -x[1]):
            bar = "█" * int(s * 20)
            print(f"    {c}: {s:.3f} {bar}")
    print("\n=== 混淆矩阵 ===")
    cmds = list(data.keys())
    print(f"{'':>10}", end="")
    for c in cmds: print(f"{c:>8}", end="")
    print()
    for c1 in cmds:
        print(f"{c1:>10}", end="")
        for c2 in cmds:
            s = results[c1][c2]
            if s > 0.80: print(f"  {'✅':>4}", end=" ")
            elif s > 0.70: print(f"  {'🟡':>4}", end=" ")
            else: print(f"  {'  ':>4}", end=" ")
        print()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "enroll": enroll_all()
        elif sys.argv[1] == "calibrate": calibrate()
        elif sys.argv[1] == "test":
            cmd = recognize()
            print(f"识别结果: {cmd or '(无)'}")
    else:
        listen_and_execute()
