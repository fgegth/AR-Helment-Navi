'''补录剩余语音命令'''
import sys, os, subprocess, time
sys.path.insert(0, '/opt/hud_navi'); os.chdir('/opt/hud_navi')
from voice_command import enroll_command

VP = '/opt/hud_navi/data/voice_prompts'

def speak(name):
    p = f'{VP}/{name}.wav'
    if os.path.exists(p):
        subprocess.run(['aplay', '-q', '-D', 'plughw:0,0', p], timeout=5)

for pk, cn in [('cmd_duoyuan', '还有多远'), ('cmd_kaishi', '开始导航')]:
    speak(pk)
    time.sleep(2)
    enroll_command(cn)
    speak('done')
    time.sleep(1)
    print(f'{cn}: OK')

print('=== 全部完成 ===')
