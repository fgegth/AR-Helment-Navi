'''语音播报工具 — 用预录WAV + aplay，不依赖gTTS'''
import subprocess, os, wave, struct

PROMPT_DIR = '/opt/hud_navi/data/voice_prompts'

# 预录提示音映射
PROMPTS = {
    'no_nav': 'cmd_fail',       #  未识别，请重试 用作当前没有导航
    'nav_cancel': 'done',       # 已录制 用作导航已取消
    'start_nav': 'cmd_ok',      # 识别成功 用作开始导航
}

def speak_wav(filepath):
    '''播一个WAV文件'''
    if not os.path.exists(filepath):
        return False
    try:
        subprocess.run(['aplay', '-q', filepath], timeout=5)
        return True
    except:
        return False

def speak_text(text):
    '''根据文本选择预录提示音播放'''
    # 简单匹配
    if '没有导航' in text:
        return speak_wav(f'{PROMPT_DIR}/cmd_fail.wav')
    elif '取消' in text:
        return speak_wav(f'{PROMPT_DIR}/done.wav')
    elif '开始导航' in text or '导航到' in text:
        return speak_wav(f'{PROMPT_DIR}/cmd_ok.wav')
    elif '剩余' in text or '公里' in text or '米' in text:
        # 距离播报: 生成TTS或用beep
        # 暂时用beep代替
        try:
            subprocess.run(['aplay', '-q', '/tmp/beep2.wav'], timeout=5)
        except:
            pass
        return True
    else:
        # 默认: 尝试aplay通用提示
        return speak_wav(f'{PROMPT_DIR}/start_speak.wav')

# 测试
if __name__ == '__main__':
    speak_text('开始导航到公司')
