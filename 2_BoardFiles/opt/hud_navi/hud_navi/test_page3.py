import urllib.request

r = urllib.request.urlopen("http://127.0.0.1:8080/", timeout=5)
h = r.read().decode("utf-8")

checks = [
    "voiceEnroll",      # JS function
    "btnVpEnroll",      # 声纹按钮
    "btnCmdEnroll",     # 命令录制按钮
    "录制声纹",          # 中文标题
    "录制命令",          # 中文标题
    "CMD_LIST",         # 命令列表
    "sleep(ms)",        # sleep helper
    "isEnrolling",      # 防重复点击
    "vpProgress",       # 进度条
]

for c in checks:
    print("%s: %s" % (c, "OK" if c in h else "MISSING"))

print("Size:", len(h))
