from map_api_c import AmapProvider
p = AmapProvider()
data = p.get_map_image((39.9087, 116.3975), 640, 400)
if data:
    with open("/tmp/amap_test.png", "wb") as f:
        f.write(data)
    print(f"Map image: {len(data)} bytes OK")
else:
    print("FAILED")
