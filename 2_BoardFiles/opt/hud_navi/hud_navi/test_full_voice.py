from voice_command import listen_and_execute

def on_cmd(cmd):
    print(f">>> 即将执行: {cmd}")

print("请说话... (VAD监听中)")
listen_and_execute(on_cmd)
