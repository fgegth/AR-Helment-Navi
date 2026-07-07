from voice_command import wait_for_voice
print("等待说话... (对着麦克风说点什么)")
ok = wait_for_voice()
print("检测到人声!" if ok else "超时")
