import urllib.request, json, time

print("Testing voice enroll (this will take ~15s)...")
t0 = time.time()
try:
    r = urllib.request.urlopen("http://127.0.0.1:8080/voice/enroll", timeout=20)
    d = json.loads(r.read())
    elapsed = time.time() - t0
    print("Result: status=%s, energy=%s, time=%.1fs" % (
        d.get("status"), d.get("energy"), elapsed))
except Exception as e:
    print("FAIL:", e)
