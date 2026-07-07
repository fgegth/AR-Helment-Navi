"""网络诊断: 为什么 Edge-TTS 时好时坏"""
import socket, ssl, time

host = "speech.platform.bing.com"
port = 443

for af_name, af in [("IPv4", socket.AF_INET), ("IPv6", socket.AF_INET6)]:
    try:
        addrs = socket.getaddrinfo(host, port, af, socket.SOCK_STREAM)
        for addr in addrs[:2]:
            ip = addr[4][0]
            try:
                t0 = time.time()
                sock = socket.create_connection((ip, port), timeout=5)
                ctx = ssl.create_default_context()
                ss = ctx.wrap_socket(sock, server_hostname=host)
                t = (time.time() - t0) * 1000
                print(f"  {af_name} {ip}: OK ({t:.0f}ms, {ss.version()})")
                ss.close()
            except Exception as e:
                print(f"  {af_name} {ip}: FAIL - {e}")
    except Exception as e:
        print(f"  {af_name} resolve FAIL: {e}")

# Fast retry test
print("\n快速连续重试 5 次...")
ok, fail = 0, 0
for i in range(5):
    try:
        sock = socket.create_connection((host, port), timeout=5)
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(sock, server_hostname=host)
        ss.close()
        ok += 1
        print(f"  #{i+1} OK")
    except Exception:
        fail += 1
        print(f"  #{i+1} FAIL")
print(f"结果: {ok}/{ok+fail} 成功")
