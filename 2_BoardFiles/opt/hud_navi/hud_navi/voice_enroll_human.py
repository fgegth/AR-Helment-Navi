'''中文人声提示语音注册 — 用 aplay 播放提示音 (避免占声卡)'''
import sys, os, subprocess, time
sys.path.insert(0, '/opt/hud_navi'); os.chdir('/opt/hud_navi')
from voice_auth import record_audio, enroll as enroll_vp
from voice_command import enroll_command

VP = '/opt/hud_navi/data/voice_prompts'


def speak(name):
    '''用 aplay 播放中文提示音 (直接 ALSA, 不经过 PulseAudio)'''
    path = f'{VP}/{name}.wav'
    if not os.path.exists(path):
        print(f'  缺少提示音: {name}.wav')
        return
    subprocess.run(['aplay', '-q', '-D', 'plughw:0,0', path], timeout=5)


# ===== 1. 声纹注册 =====
print('=== 声纹注册 ===')
speak('enroll_vp')
time.sleep(1.5)
record_audio('/tmp/enroll_vp.wav', 3)
enroll_vp('owner', 3)
speak('done')
print('声纹: OK')
time.sleep(1)

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
    time.sleep(1.5)
    enroll_command(cmd_name)
    speak('done')
    time.sleep(0.8)
    print(f'{cmd_name}: OK')

# ===== 3. 完成 =====
speak('all_done')
time.sleep(1)
print('=== 全部注册完成 ===')
