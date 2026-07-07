from voice_auth import record_audio, read_wav
from voice_command import _rms

# 录 2 秒环境音
print("1. 安静 2 秒 (测环境噪音)...")
record_audio("/tmp/silence.wav", 2)
sil = read_wav("/tmp/silence.wav")
print(f"   环境噪音 RMS: {_rms(sil):.5f}")

# 录 2 秒说话
print("2. 现在说话! (2 秒)...")
record_audio("/tmp/speech.wav", 2)
sp = read_wav("/tmp/speech.wav")
print(f"   说话 RMS: {_rms(sp):.5f}")

ratio = _rms(sp) / (_rms(sil) + 0.0001)
print(f"   信噪比: {ratio:.1f}x")
print(f"   建议阈值: {_rms(sil) * 3:.5f}")
