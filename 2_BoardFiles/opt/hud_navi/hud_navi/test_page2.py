import urllib.request

r = urllib.request.urlopen("http://127.0.0.1:8080/", timeout=5)
h = r.read().decode("utf-8")

checks = ["voiceEnroll", "声纹录制", "btnVpEnroll", "btnCmdEnroll",
           "音频输出", "btnAudioAuto", "quickNav", "HARDCODED_PLACES"]

for c in checks:
    print("%s: %s" % (c, "OK" if c in h else "MISSING"))

print("Size:", len(h))
