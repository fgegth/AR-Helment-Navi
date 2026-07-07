"""
v5.0 模块综合测试 — 无需硬件的功能验证
"""
import sys
import os

print("=" * 60)
print("  HUD v5.0 模块测试")
print("=" * 60)

errors = []

# --- 1. 配置 ---
print("\n[1/6] config.py")
try:
    from config import (
        ASR_PROVIDER, ASR_ALIYUN_APPKEY, ASR_ALIYUN_ACCESS_KEY,
        TTS_PROVIDER, TTS_EDGE_VOICE,
    )
    print(f"  ASR Provider: {ASR_PROVIDER}")
    print(f"  ASR AppKey:   {'***configured***' if ASR_ALIYUN_APPKEY else 'MISSING'}")
    print(f"  ASR AccessKey:{'***configured***' if ASR_ALIYUN_ACCESS_KEY else 'MISSING'}")
    print(f"  TTS Provider: {TTS_PROVIDER}")
    print(f"  TTS Voice:    {TTS_EDGE_VOICE}")
except Exception as e:
    errors.append(f"config: {e}")
    print(f"  FAIL: {e}")

# --- 2. ASR 云模块 ---
print("\n[2/6] asr_cloud.py")
try:
    from asr_cloud import CloudASR, match_command, COMMAND_PATTERNS

    # 测试命令匹配
    tests = [
        ("开始导航去公司", "开始导航"),
        ("我想回家", "回家"),
        ("还有多远才能到", "还有多远"),
        ("取消导航不去了", "取消导航"),
        ("随便说点什么", ""),
    ]
    all_ok = True
    for text, expected in tests:
        result = match_command(text)
        status = "OK" if result == expected else f"FAIL (got '{result}')"
        if result != expected:
            all_ok = False
        print(f"  '{text}' -> '{result}' [{status}]")

    # 测试 ASR 初始化
    asr = CloudASR()
    print(f"  CloudASR init: provider={asr.provider}, available={asr.is_available()}")

    if not all_ok:
        errors.append("asr_cloud: command matching failed")
except Exception as e:
    errors.append(f"asr_cloud: {e}")
    print(f"  FAIL: {e}")

# --- 3. Edge-TTS ---
print("\n[3/6] tts_edge.py")
try:
    from tts_edge import EdgeTTS, speak_quick

    tts = EdgeTTS()
    if tts.is_available():
        path = tts.save_to_file("前方300米左转进入长安街")
        if path and os.path.exists(path):
            size = os.path.getsize(path)
            print(f"  EdgeTTS: OK (generated {size} bytes)")
        else:
            print(f"  EdgeTTS: FAIL (no output)")
            errors.append("tts_edge: failed to generate MP3")
    else:
        print(f"  EdgeTTS: NOT AVAILABLE (pip install edge-tts)")
        errors.append("tts_edge: not available")
except Exception as e:
    errors.append(f"tts_edge: {e}")
    print(f"  FAIL: {e}")

# --- 4. voice_command ---
print("\n[4/6] voice_command.py")
try:
    from voice_command import match_command_text, COMMAND_PATTERNS
    print(f"  Commands: {len(COMMAND_PATTERNS)} patterns")
    print(f"  match_command_text import: OK")
except Exception as e:
    errors.append(f"voice_command: {e}")
    print(f"  FAIL: {e}")

# --- 5. speak_helper ---
print("\n[5/6] speak_helper.py")
try:
    from speak_helper import speak, speak_async, pre_cache_phrases
    from speak_helper import PROMPTS, EDGE_CACHE_DIR
    print(f"  Prompts: {[k for k in PROMPTS if PROMPTS[k]]}")
    print(f"  Cache dir: {EDGE_CACHE_DIR}")
    print(f"  speak_helper import: OK")
except Exception as e:
    errors.append(f"speak_helper: {e}")
    print(f"  FAIL: {e}")

# --- 6. HTTP Server ---
print("\n[6/6] http_server.py")
try:
    from http_server import HTML_PAGE, Handler, start_server
    # 验证 HTML 关键片段
    assert "quickNav" in HTML_PAGE, "quickNav function missing"
    assert "HARDCODED_PLACES" in HTML_PAGE, "hardcoded places missing"
    assert "voice/command" in HTML_PAGE, "voice command tab missing"
    print(f"  HTML page: {len(HTML_PAGE)} chars")
    print(f"  Start-server function: OK")
    print(f"  Handler class: OK")
except Exception as e:
    errors.append(f"http_server: {e}")
    print(f"  FAIL: {e}")

# --- 结果 ---
print("\n" + "=" * 60)
if errors:
    print(f"  {len(errors)} error(s):")
    for e in errors:
        print(f"    - {e}")
else:
    print("  ALL TESTS PASSED")
print("=" * 60)
