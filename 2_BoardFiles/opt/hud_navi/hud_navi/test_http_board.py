"""测试板子上 HTTP 服务是否正常"""
import urllib.request
import json

# Test ping
try:
    r = urllib.request.urlopen("http://localhost:8080/ping", timeout=5)
    print("Ping:", json.loads(r.read()))
except Exception as e:
    print("Ping FAIL:", e)

# Test status
try:
    r = urllib.request.urlopen("http://localhost:8080/status", timeout=5)
    data = json.loads(r.read())
    print("Status: lat=%s lon=%s speed=%s navigating=%s" % (
        data.get("lat"), data.get("lon"),
        data.get("speed"), data.get("is_navigating"),
    ))
except Exception as e:
    print("Status FAIL:", e)

# Test places
try:
    r = urllib.request.urlopen("http://localhost:8080/places", timeout=5)
    data = json.loads(r.read())
    print("Places:", len(data.get("places", [])), "found")
except Exception as e:
    print("Places FAIL:", e)

# Test voice command
try:
    import json as j
    req = urllib.request.Request(
        "http://localhost:8080/voice/command",
        data=j.dumps({"text": "开始导航"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    r = urllib.request.urlopen(req, timeout=5)
    print("Voice Cmd:", json.loads(r.read()))
except Exception as e:
    print("Voice Cmd FAIL:", e)

print("\n=== HTTP server is working! ===")
print("Phone URL: http://10.214.52.161:8080")
