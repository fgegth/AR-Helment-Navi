import json
with open('/opt/hud_navi/data/commands.json') as f:
    data = json.load(f)
for cmd, feats in data.items():
    print(f'{cmd}: {len(feats)}维')
print('---')
with open('/opt/hud_navi/data/voiceprint.json') as f:
    vp = json.load(f)
print(f'声纹: {len(vp.get("owner",[]))}维')
print('---')
print('全部数据一致!' if all(len(f)==16 for f in data.values()) else '存在不一致!')
