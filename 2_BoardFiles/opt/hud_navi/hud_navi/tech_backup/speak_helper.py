'''播报辅助: 在 pygame HUD 运行时也能播放 WAV'''
import wave, struct, os

PROMPT_DIR = '/opt/hud_navi/data/voice_prompts'

PROMPTS = {
    '没有导航': 'cmd_fail.wav',
    '取消': 'done.wav',
    '开始导航': 'cmd_ok.wav',
    '公司': 'cmd_qugongsi.wav',
    '家': 'cmd_huijia.wav',
    '到达': 'all_done.wav',
    '超速': None,
    '距离': None,
}

def speak(text):
    '''用 aplay 播放，避开 pygame 声卡冲突'''
    import subprocess
    fname = None
    for key, val in PROMPTS.items():
        if key in text:
            fname = val
            break
    if fname:
        path = f'{PROMPT_DIR}/{fname}'
    else:
        # 距离/其他播报：生成临时 beep
        _make_beep('/tmp/speak_beep.wav')
        path = '/tmp/speak_beep.wav'
    
    try:
        subprocess.run(['aplay', '-q', path], timeout=5)
    except:
        pass

def _make_beep(path, freq=440, duration=0.3):
    '''生成提示音 WAV'''
    sr = 16000
    import math
    frames = []
    for i in range(int(sr * duration)):
        val = int(8000 * math.sin(2 * math.pi * freq * i / sr))
        frames.append(struct.pack('<hh', val, val))
    wf = wave.open(path, 'wb')
    wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(sr)
    wf.writeframes(b''.join(frames)); wf.close()
