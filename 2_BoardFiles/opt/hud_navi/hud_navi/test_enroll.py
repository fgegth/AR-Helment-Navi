import urllib.request

# Test voice enroll endpoint
print("Testing voice enroll...")
r = urllib.request.urlopen("http://127.0.0.1:8080/voice/enroll", timeout=5)
data = eval(r.read().decode())
print("Result:", data)

# Wait for recording to finish
import time
time.sleep(5)

# Check if voiceprint file was created
import os
vp_file = "/opt/hud_navi/data/voiceprint.json"
if os.path.exists(vp_file):
    import json
    with open(vp_file) as f:
        vp = json.load(f)
    print("Voiceprint saved:", list(vp.keys()))
    for name, feats in vp.items():
        print("  %s: %d features" % (name, len(feats)))
else:
    print("Voiceprint file NOT found!")
