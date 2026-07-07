"""
璇煶鍛戒护璇嗗埆 鈥?Whisper NPU 浼樺厛 / 妯℃澘鍖归厤闄嶇骇 + 浜哄０妫€娴嬭Е鍙?鍛戒护闆? "鍘诲叕鍙? "鍥炲" "鍙栨秷瀵艰埅" "杩樻湁澶氳繙" "寮€濮嬪鑸?
瑙﹀彂鏂瑰紡: 妫€娴嬪埌浜哄０ 鈫?鑷姩褰曢煶 鈫?澹扮汗楠岃瘉 鈫?鍛戒护璇嗗埆
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
    """鎾斁纭闊? 鐭績楂橀beep鍛婅瘔鐢ㄦ埛"鍚埌浜? 姝ｅ湪澶勭悊" (闈為樆濉?"""
    import struct as _st
    rate, freq, dur = 16000, 1200, 1
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
    """闃诲绛夊緟鐩村埌妫€娴嬪埌浜哄０"""
    with audio_lock:
        record_audio("/tmp/vad_baseline.wav", 1)
    baseline = _rms(read_wav("/tmp/vad_baseline.wav"))
    threshold = min(max(baseline * 1.5, 0.003), 0.030)
    print(f"  VAD 鍩虹嚎: {baseline:.4f}, 闃堝€? {threshold:.4f}")
    while True:
        with audio_lock:
            record_audio("/tmp/vad_check.wav", 1)
            energy = _rms(read_wav("/tmp/vad_check.wav"))
        if energy > threshold:
            return True
        time.sleep(0.3)

# 棰勭疆鍛戒护妯℃澘
DEFAULT_COMMANDS = ["鍘诲叕鍙?, "鍥炲", "鍙栨秷瀵艰埅", "杩樻湁澶氳繙", "寮€濮嬪鑸?]

def enroll_command(name: str):
    print(f"褰曞埗鍛戒护: {name} (璇疯銆寋name}銆?")
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
    print(f"  鉁?宸插綍鍒? {name}")

def enroll_all():
    print("=== 璇煶鍛戒护褰曞埗 (璇烽€愪釜璇翠互涓嬪懡浠? ===")
    for cmd in DEFAULT_COMMANDS:
        enroll_command(cmd)
    # 閫掑璁℃暟锛圓PK 杞鐢級+ 鍘熷瓙鍐欏叆闃插啿绐?    if os.path.exists(CMD_FILE):
        with open(CMD_FILE) as f: data = json.load(f)
        data["_count"] = int(data.get("_count", 0)) + 1
        tmp = CMD_FILE + ".tmp"
        with open(tmp, "w") as f: json.dump(data, f)
        os.replace(tmp, CMD_FILE)
    print("=== 鍏ㄩ儴褰曞埗瀹屾垚 ===")

def recognize(timeout: float = 0.75) -> str:
    if not os.path.exists(CMD_FILE):
        print("鏃犲懡浠ゆā鏉匡紝璇峰厛杩愯 enroll_all()")
        return ""
    try:
        with open(CMD_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError):
        print("鍛戒护妯℃澘鎹熷潖, 璇烽噸鏂板綍鍏?)
        return ""
    with audio_lock:
        record_audio("/tmp/cmd_unknown.wav", 2)
    samples = read_wav("/tmp/cmd_unknown.wav")
    feats = extract_features(samples)
    best_cmd, best_sim = "", 0
    for cmd, template in data.items():
        if cmd.startswith("_"): continue  # 璺宠繃 _count 绛夊厓鏁版嵁
        sim = cosine_sim(feats, template)
        if sim > best_sim: best_sim, best_cmd = sim, cmd
    print(f"  璇嗗埆: {best_cmd} (鐩镐技搴?{best_sim:.3f})")
    return best_cmd if best_sim >= timeout else ""

def listen_loop(callback=None):
    """鎸佺画鐩戝惉寰幆 (姘镐笉閫€鍑? 鍚庡彴绾跨▼)"""
    print("璇煶鐩戝惉宸插惎鍔?(妫€娴嬩汉澹拌嚜鍔ㄨЕ鍙?...")
    while True:
        try:
            if not wait_for_voice():
                time.sleep(0.5); continue
            print("馃帳 妫€娴嬪埌浜哄０锛屾鍦ㄨ瘑鍒?..")
            record_audio("/tmp/voice_input.wav", 2)
            # 澹扮汗楠岃瘉
            from voice_auth import verify
            if not verify("owner", 0.70, 2):
                print("鉂?澹扮汗涓嶅尮閰?); continue
            # 鍛戒护璇嗗埆
            samples = read_wav("/tmp/voice_input.wav")
            feats = extract_features(samples)
            with open(CMD_FILE) as f: data = json.load(f)
            best_cmd, best_sim = "", 0
            for cmd, template in data.items():
                if cmd.startswith("_"): continue  # 璺宠繃 _count 绛夊厓鏁版嵁
                sim = cosine_sim(feats, template)
                if sim > best_sim: best_sim, best_cmd = sim, cmd
            if best_sim >= 0.80:
                print(f"鉁?鎵ц: {best_cmd} ({best_sim:.2f})")
                if callback: callback(best_cmd)
            else: print(f"鉂?鏈瘑鍒?({best_sim:.2f})")
        except Exception as e:
            print(f"璇煶鐩戝惉寮傚父: {e}"); time.sleep(1)

def listen_and_execute(callback=None):
    """
    鍗曟璇嗗埆娴佺▼ 鈥?鍞ら啋璇?+ Vosk璇嗗埆 + 鎰忓浘瑙ｆ瀽
    V3.2: 鍞ら啋璇?浣犲ソ灏忓痉", 骞虫椂鑱婂ぉ涓嶈Е鍙? 鍞ら啋鍚?0绉掑唴鍝嶅簲鍛戒护
    """
    wav_file = "/tmp/voice_input.wav"
    RECORD_SEC = 5
    WAKE_WORDS = ["灏忔灏忔", "灏忔", "灏忛晣灏忛晣", "灏忛晣", "灏忛儜灏忛儜", "灏忕湡灏忕湡", "灏忔斂灏忔斂"]  # 鍚岄煶瀛楀閿?    COMMAND_TIMEOUT = 10  # 鍞ら啋鍚庢湁鏁堝懡浠ょ獥鍙?绉?

    # 鍒濆鍖栧敜閱掔姸鎬?    if not hasattr(listen_and_execute, "_wake_until"):
        listen_and_execute._wake_until = 0  # 鍛戒护绐楀彛鎴鏃堕棿

    with audio_lock:
        record_audio(wav_file, RECORD_SEC)
    samples = read_wav(wav_file)
    rms = _rms(samples)
    # 鍔ㄦ€侀槇鍊?    if not hasattr(listen_and_execute, "_baseline"):
        listen_and_execute._baseline = max(rms, 0.001)
        return ""
    threshold = min(max(listen_and_execute._baseline * 1.5, 0.003), 0.030)
    # 缂撴參鏇存柊鍩虹嚎锛堥€傚簲鐜鍣煶鍙樺寲锛?    listen_and_execute._baseline = listen_and_execute._baseline * 0.95 + rms * 0.05
    if rms < threshold or len(samples) < 1000:
        return ""  # 浣庝簬闃堝€硷紝璺宠繃
    # 馃幍 绔嬪嵆鍙嶉: 鎾斁杞昏Е闊冲憡璇夌敤鎴?鍚埌浜? 姝ｅ湪澶勭悊"
    threading.Thread(target=lambda: _play_ack_beep(), daemon=True).start()
    print(f"馃帳 璇嗗埆涓?.. (RMS={rms:.4f}, 闃堝€?{threshold:.4f}, 褰曢煶{RECORD_SEC}s)")
    feats = extract_features(samples)
    # 澹扮汗楠岃瘉
    from voice_auth import FEAT_FILE, cosine_sim as cs
    vp_sim = 0
    if os.path.exists(FEAT_FILE):
        try:
            with open(FEAT_FILE) as f:
                vp_data = json.load(f)
            if "owner" in vp_data:
                vp_sim = cs(feats, vp_data["owner"])
        except (json.JSONDecodeError, ValueError):
            pass  # 澹扮汗鏁版嵁鎹熷潖, 璺宠繃楠岃瘉
        if vp_sim < 0.50:
            print(f"鉂?澹扮汗涓嶅尮閰?({vp_sim:.3f})"); return ""
    # 妯℃澘鍖归厤 (MFCC 瀵瑰凡璁粌鐨勭煭鍛戒护姣?Whisper 鏇村彲闈?
    tpl_cmd, tpl_sim = "", 0
    if os.path.exists(CMD_FILE):
        try:
            with open(CMD_FILE) as f:
                cmd_data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            cmd_data = {}
        for c, tpl in cmd_data.items():
            if c.startswith("_"): continue
            s = cs(feats, tpl)
            if s > tpl_sim: tpl_sim, tpl_cmd = s, c
    # Vosk 绂荤嚎璇煶璇嗗埆 (绾疌PU, 50MB妯″瀷)
    vosk_text = ""
    try:
        from hud_vosk import audio_to_text_vosk
        vosk_text = audio_to_text_vosk(wav_file)
    except Exception: pass
    vosk_text = (vosk_text or "").strip().replace(" ", "")  # Vosk杈撳嚭甯︾┖鏍? 鍘绘帀

    now = time.time()
    is_wake = any(w in vosk_text for w in WAKE_WORDS)

    # 鍞ら啋璇嶆娴?    if is_wake:
        listen_and_execute._wake_until = now + COMMAND_TIMEOUT
        print(f"馃敂 鍞ら啋! 鍛戒护绐楀彛: {COMMAND_TIMEOUT}绉?)
        # 鎾斁鍞ら啋鎻愮ず闊?        try:
            import subprocess as _sp
            _sp.run(["aplay", "-q", "-D", AUDIO_DEVICE, "/opt/hud_navi/data/voice_prompts/start_speak.wav"], timeout=2)
        except: pass
        return ""

    # 鍛戒护绐楀彛杩囨湡 鈫?蹇界暐
    if now > listen_and_execute._wake_until:
        if vosk_text:
            print(f"  [蹇界暐] {vosk_text} (鏈敜閱?")
        return ""

    # 鍛戒护绐楀彛鍐?鈫?姝ｅ父璇嗗埆
    if vosk_text:
        best_cmd = vosk_text
        print(f"  Vosk: {vosk_text}")
    elif tpl_sim >= 0.80:
        best_cmd = tpl_cmd
        print(f"  妯℃澘鍖归厤: {tpl_cmd} ({tpl_sim:.2f})")
    else:
        best_cmd = ""
    if best_cmd:
        print(f"鉁?鎵ц: {best_cmd}")
        if callback: callback(best_cmd)
        return best_cmd
    else:
        print("鉂?鏈瘑鍒?); return ""

def calibrate():
    if not os.path.exists(CMD_FILE):
        print("璇峰厛鎵ц enroll 褰曞埗鍛戒护"); return
    with open(CMD_FILE) as f: data = json.load(f)
    print("=== 鍛戒护鏍″噯 (閫愪釜璇村懡浠わ紝姣忎釜 2 绉? ===")
    results = {}
    for cmd in data:
        input(f"鍑嗗濂借銆寋cmd}銆嶏紝鎸夊洖杞﹀紑濮?..")
        record_audio("/tmp/cal.wav", 2)
        feats = extract_features(read_wav("/tmp/cal.wav"))
        scores = {}
        for tcmd, tpl in data.items():
            scores[tcmd] = cosine_sim(feats, tpl)
        results[cmd] = scores
        best = max(scores, key=scores.get)
        icon = "鉁? if best == cmd else "鉂?
        print(f"  {icon} 璇寸殑鏄€寋cmd}銆嶁啋 璇嗗埆涓恒€寋best}銆?)
        for c, s in sorted(scores.items(), key=lambda x: -x[1]):
            bar = "鈻? * int(s * 20)
            print(f"    {c}: {s:.3f} {bar}")
    print("\n=== 娣锋穯鐭╅樀 ===")
    cmds = list(data.keys())
    print(f"{'':>10}", end="")
    for c in cmds: print(f"{c:>8}", end="")
    print()
    for c1 in cmds:
        print(f"{c1:>10}", end="")
        for c2 in cmds:
            s = results[c1][c2]
            if s > 0.80: print(f"  {'鉁?:>4}", end=" ")
            elif s > 0.70: print(f"  {'馃煛':>4}", end=" ")
            else: print(f"  {'  ':>4}", end=" ")
        print()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "enroll": enroll_all()
        elif sys.argv[1] == "calibrate": calibrate()
        elif sys.argv[1] == "test":
            cmd = recognize()
            print(f"璇嗗埆缁撴灉: {cmd or '(鏃?'}")
    else:
        listen_and_execute()

