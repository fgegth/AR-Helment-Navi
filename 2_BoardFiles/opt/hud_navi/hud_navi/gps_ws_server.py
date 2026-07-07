"""
GPS WebSocket 服务 — 读取串口 NMEA → WebSocket 推送坐标
前端 HTML 通过 ws://127.0.0.1:8088 接收实时 GPS 数据
"""
import asyncio, json, math, serial, time
import websockets

PORT = "/dev/ttyS8"
BAUD = 9600
WS_PORT = 8088

clients = set()

def parse_nmea_rmc(line: str) -> dict:
    """解析 $GPRMC → {lat, lon, speed, course}"""
    parts = line.split(",")
    if len(parts) < 9 or parts[2] != "A":
        return None
    try:
        lat_raw = parts[3]; lon_raw = parts[5]
        lat = float(lat_raw[:2]) + float(lat_raw[2:]) / 60
        lon = float(lon_raw[:3]) + float(lon_raw[3:]) / 60
        if parts[4] == "S": lat = -lat
        if parts[6] == "W": lon = -lon
        speed = float(parts[7]) * 1.852 if parts[7] else 0  # 节 → km/h
        course = float(parts[8]) if parts[8] else 0
        return {"lat": round(lat, 7), "lon": round(lon, 7), "speed": speed, "course": course}
    except (ValueError, IndexError):
        return None

async def handler(websocket):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.remove(websocket)

async def broadcast(data: dict):
    if clients:
        msg = json.dumps(data)
        await asyncio.gather(*[ws.send(msg) for ws in clients])

async def gps_loop():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=0.5)
    except Exception as e:
        print(f"GPS串口打开失败: {e}")
        return

    buf = ""
    while True:
        try:
            raw = ser.read(256).decode("ascii", errors="ignore")
            buf += raw
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if "$GPRMC" in line or "$GNRMC" in line:
                    data = parse_nmea_rmc(line)
                    if data:
                        await broadcast(data)
        except Exception:
            pass
        await asyncio.sleep(0.15)

async def main():
    print(f"GPS WebSocket 服务: ws://127.0.0.1:{WS_PORT}")
    async with websockets.serve(handler, "127.0.0.1", WS_PORT):
        await gps_loop()

if __name__ == "__main__":
    asyncio.run(main())
