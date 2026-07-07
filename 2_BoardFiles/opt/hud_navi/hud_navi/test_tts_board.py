from tts_edge import EdgeTTS
import os

tts = EdgeTTS()
path = tts.save_to_file("你好 HUD导航系统已就绪")
if path:
    size = os.path.getsize(path)
    print("TTS: OK (%d bytes)" % size)
else:
    print("TTS: FAIL")
