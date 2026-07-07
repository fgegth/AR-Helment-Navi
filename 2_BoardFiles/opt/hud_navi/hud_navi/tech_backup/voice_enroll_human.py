'''中文人声提示语音注册'''
import sys, os, struct
os.environ['SDL_AUDIODRIVER'] = 'alsa'
os.environ['AUDIODEV'] = 'hw:0,0'
sys.path.insert(0, '/opt/hud_navi'); os.chdir('/opt/hud_navi')
import pygame, wave
from voice_auth import record_audio, enroll as enroll_vp
from voice_command import enroll_command

VP = 'data/voice_prompts'
pygame.mixer.init(frequency=22050, size=-16, channels=2)

def speak(name):
    '''播放中文提示音 (自动 mono→stereo)'''
    wf = wave.open(f'{VP}/{name}.wav', 'rb')
    n = wf.getnframes()
    raw = wf.readframes(n)
    wf.close()
    # mono → stereo
    stereo = b''
    for i in range(0, len(raw), 2):
        sample = raw[i:i+2]
        stereo += sample + sample
    snd = pygame.mixer.Sound(buffer=stereo)
    snd.play()
    # 等待播放完毕
    duration = n / 22050.0
    pygame.time.wait(int(duration * 1000) + 200)

# ===== 1. 声纹注册 =====
print('=== 声纹注册 ===')
speak('enroll_vp')
pygame.time.wait(300)
# 用 arecord 录音
record_audio('/tmp/enroll_vp.wav', 3)
enroll_vp('owner', 3)
speak('done')
print('声纹: OK')
pygame.time.wait(400)

# ===== 2. 命令注册 =====
cmds = [
    ('cmd_qugongsi', '去公司'),
    ('cmd_huijia', '回家'),
    ('cmd_quxiao', '取消导航'),
    ('cmd_duoyuan', '还有多远'),
    ('cmd_kaishi', '开始导航'),
]
print('=== 命令注册 ===')
for prompt_key, cmd_name in cmds:
    speak(prompt_key)
    pygame.time.wait(400)
    enroll_command(cmd_name)
    speak('done')
    print(f'{cmd_name}: OK')
    pygame.time.wait(400)

# ===== 3. 完成 =====
speak('all_done')
print('=== 全部注册完成 ===')
pygame.quit()
