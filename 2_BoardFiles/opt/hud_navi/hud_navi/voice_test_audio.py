'''带提示音的语音识别测试'''
import sys, os, math, struct, json
os.environ['SDL_AUDIODRIVER'] = 'alsa'
os.environ['AUDIODEV'] = 'hw:0,0'
sys.path.insert(0, '/opt/hud_navi'); os.chdir('/opt/hud_navi')
import pygame
from voice_command import wait_for_voice, _rms
from voice_auth import record_audio, read_wav, extract_features, cosine_sim

pygame.mixer.init(frequency=16000, size=-16, channels=2)

def beep(freq=660, duration=0.25):
    sr = 16000; samples = []
    for i in range(int(sr * duration)):
        val = int(6000 * math.sin(2 * math.pi * freq * i / sr))
        samples.append(struct.pack('<hh', val, val))
    snd = pygame.mixer.Sound(buffer=b''.join(samples))
    snd.play(); pygame.time.wait(int(duration * 1000) + 100)

def low_beep(): beep(220, 0.5)   # 失败
def high_beep(): beep(880, 0.3)  # 成功

print('等待说话... (检测人声自动触发)')
ok = wait_for_voice()
if not ok:
    print('未检测到')
    pygame.quit(); exit()

beep(660, 0.15)  # 确认检测到
print('识别中...')
record_audio('/tmp/test_cmd.wav', 2)
samples = read_wav('/tmp/test_cmd.wav')
feats = extract_features(samples)

# 声纹
with open('data/voiceprint.json') as f:
    vp = json.load(f)
vp_sim = cosine_sim(feats, vp['owner'])

# 命令
with open('data/commands.json') as f:
    cmds = json.load(f)
best_cmd, best_sim = '', 0
for cmd, tpl in cmds.items():
    sim = cosine_sim(feats, tpl)
    print(f'  {cmd}: {sim:.3f}')
    if sim > best_sim:
        best_sim, best_cmd = sim, cmd

print(f'声纹: {vp_sim:.3f}  命令: {best_cmd} ({best_sim:.3f})')

vp_ok = vp_sim >= 0.45
cmd_ok = best_sim >= 0.70

if vp_ok and cmd_ok:
    high_beep(); pygame.time.wait(150); high_beep()
    print(f'✅ 识别成功: {best_cmd}')
elif not vp_ok:
    low_beep()
    print(f'❌ 声纹不匹配 ({vp_sim:.3f})')
else:
    low_beep(); pygame.time.wait(300); beep(440, 0.3)
    print(f'❓ 命令不明确 ({best_cmd}:{best_sim:.3f})')

pygame.quit()
