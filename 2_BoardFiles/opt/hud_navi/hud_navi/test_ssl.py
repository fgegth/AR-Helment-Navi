import ssl, socket, sys
print("Python:", sys.version)
print("SSL:", ssl.OPENSSL_VERSION)

# Test connection
try:
    ctx = ssl.create_default_context()
    sock = socket.create_connection(("speech.platform.bing.com", 443), timeout=10)
    ssock = ctx.wrap_socket(sock, server_hostname="speech.platform.bing.com")
    print("SSL connection:", ssock.version())
    ssock.close()
except Exception as e:
    print("SSL FAIL:", e)

# Test edge-tts directly
try:
    from edge_tts import Communicate
    import asyncio
    async def test():
        try:
            comm = Communicate("测试", "zh-CN-XiaoxiaoNeural")
            await comm.save("/tmp/edge_test.mp3")
            import os
            size = os.path.getsize("/tmp/edge_test.mp3")
            print(f"Edge-TTS direct: OK ({size} bytes)")
        except Exception as e:
            print(f"Edge-TTS direct FAIL: {e}")
    asyncio.run(test())
except Exception as e:
    print("Edge-TTS import FAIL:", e)
