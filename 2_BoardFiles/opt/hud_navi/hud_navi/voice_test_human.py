'''中文人声语音识别测试'''
import sys, os, struct, json
os.environ['SDL_AUDIODRIVER'] = 'alsa'
os.environ['AUDIODEV'] = 'hw:0,0'
sys.path.insert(0, '/opt/hud_navi'); os.chdir('/opt/hud_navi')
import pygame, wave
from voice_command import wait_for_voice
from voice_auth import record_audio, read_wav, extract_features, cosine_sim

VP = 'data/voice_prompts'
pygame.mixer.init(frequency=22050, size=-16, channels=2)

def speak(name):
    wf = wave.open(f'{VP}/{name}.wav', 'rb')
    n = wf.getnframes(); raw = wf.readframes(n); wf.close()
    stereo = b''
    for i in range(0, len(raw), 2):
        stereo += raw[i:i+2] + raw[i:i+2]
    snd = pygame.mixer.Sound(buffer=stereo)
    snd.play()
    pygame.time.wait(int(n / 22050.0 * 1000) + 200)

print('等待语音命令...')
speak('start_speak')

if not wait_for_voice():
    print('未检测到声音')
    pygame.quit(); exit()

record_audio('/tmp/test_cmd.wav', 2)
samples = read_wav('/tmp/test_cmd.wav')
feats = extract_features(samples)

# 声纹
with open('data/voiceprint.json') as f:
    vp = json.load(f)
vp_sim = cosine_sim(feats, vp['owner'])
print(f'声纹: {vp_sim:.3f}')

# 命令
with open('data/commands.json') as f:
    cmds = json.load(f)
best_cmd, best_sim = '', 0
for cmd, tpl in cmds.items():
    sim = cosine_sim(feats, tpl)
    print(f'  {cmd}: {sim:.3f}')
    if sim > best_sim:
        best_sim, best_cmd = sim, cmd

vp_ok = vp_sim >= 0.45
cmd_ok = best_sim >= 0.70

print(f'结果: vp={vp_sim:.2f} cmd={best_cmd}({best_sim:.2f})')

if vp_ok and cmd_ok:
    speak('cmd_ok')
    print(f'✅ {best_cmd}')
else:
    speak('cmd_fail')
    print(f'❌ vp:{vp_ok} cmd:{cmd_ok}')

pygame.quit()
