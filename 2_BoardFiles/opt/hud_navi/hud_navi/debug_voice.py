from voice_auth import *
import json

# 检查注册的声音文件
print("=== 检查注册数据 ===")
with open(FEAT_FILE) as f:
    data = json.load(f)
print("注册特征:", [f"{v:.4f}" for v in data["owner"][:8]])

# 录一段新音频检查
record_audio("/tmp/debug.wav", 2)
samples = read_wav("/tmp/debug.wav")
print(f"采样数: {len(samples)}")
print(f"前10个值: {[f'{s:.4f}' for s in samples[:10]]}")
print(f"最大值: {max(samples):.4f}, 最小值: {min(samples):.4f}")

# 提取特征
feats = extract_features(samples)
print(f"特征向量: {[f'{f:.4f}' for f in feats[:8]]}")
sim = cosine_sim(feats, data["owner"])
print(f"相似度: {sim:.4f}")
