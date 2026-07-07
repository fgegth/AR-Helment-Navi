import urllib.request
import json

# Test GET
r = urllib.request.urlopen("http://127.0.0.1:8080/audio/status", timeout=5)
data = json.loads(r.read())
print("Audio Status: %s -> %s (override=%s)" % (
    data.get("active_type"), data.get("active_output"), data.get("override")))
print("  Headphone: %s, Devices: %d" % (
    data.get("headphone_jack"), len(data.get("devices", []))))

# Test POST with longer timeout
for mode in ["wired", "auto"]:
    req = urllib.request.Request(
        "http://127.0.0.1:8080/audio/set",
        data=json.dumps({"mode": mode}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    r2 = urllib.request.urlopen(req, timeout=15)
    d2 = json.loads(r2.read())
    print("  set '%s' -> %s" % (mode, d2.get("active_type")))

print("\nAll OK!")
