'''带提示音的语音注册 — 声纹 + 5条命令'''
import sys, os, math, struct, wave
os.environ['SDL_AUDIODRIVER'] = 'alsa'
os.environ['AUDIODEV'] = 'hw:0,0'
sys.path.insert(0, '/opt/hud_navi'); os.chdir('/opt/hud_navi')
import pygame
from voice_auth import record_audio, read_wav, extract_features, enroll as enroll_vp
from voice_command import enroll_command

pygame.mixer.init(frequency=16000, size=-16, channels=2)

def beep(freq=660, duration=0.25):
    sr = 16000
    samples = []
    for i in range(int(sr * duration)):
        val = int(6000 * math.sin(2 * math.pi * freq * i / sr))
        samples.append(struct.pack('<hh', val, val))
    snd = pygame.mixer.Sound(buffer=b''.join(samples))
    snd.set_volume(0.4)
    snd.play()
    pygame.time.wait(int(duration * 1000) + 100)

def double_beep():
    beep(880, 0.1); pygame.time.wait(100); beep(880, 0.1)

# ===== 1. 声纹注册 =====
print('=== 声纹注册 ===')
beep(440, 0.5)
pygame.time.wait(800)
beep(660, 0.3)  # 提示开始说话
enroll_vp('owner', 3)
double_beep()
print('声纹: OK')
pygame.time.wait(500)

# ===== 2. 命令注册 =====
cmds = ['去公司', '回家', '取消导航', '还有多远', '开始导航']
print('=== 命令注册 ===')
for cmd in cmds:
    beep(660, 0.3)
    pygame.time.wait(600)
    beep(880, 0.15)  # 提示开始说
    enroll_command(cmd)
    double_beep()
    print(f'{cmd}: OK')
    pygame.time.wait(400)

# ===== 完成 =====
beep(440, 0.2); pygame.time.wait(150)
beep(660, 0.2); pygame.time.wait(150)
beep(880, 0.3)
print('=== 全部注册完成 ===')
pygame.quit()
