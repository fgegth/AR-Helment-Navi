import urllib.request

# Fetch the page and check for voice tab content
r = urllib.request.urlopen("http://127.0.0.1:8080/", timeout=5)
html = r.read().decode("utf-8")

checks = [
    ("语音面板标签", "语音"),
    ("音频输出面板", "音频输出"),
    ("快捷导航按钮", "quickNav"),
    ("硬编码兜底坐标", "HARDCODED_PLACES"),
    ("语音命令面板", "voice/command"),
    ("音频状态API", "audio/status"),
    ("自动检测按钮", "btnAudioAuto"),
    ("有线按钮", "btnAudioWired"),
    ("蓝牙按钮", "btnAudioBt"),
    ("板载按钮", "btnAudioBoard"),
]

all_ok = True
for name, keyword in checks:
    found = keyword in html
    status = "OK" if found else "MISSING!"
    if not found:
        all_ok = False
    print("  [%s] %s: %s" % (status, name, keyword[:40]))

print("\nPage size: %d bytes" % len(html))
print("Result: %s" % ("ALL OK" if all_ok else "SOME MISSING"))
