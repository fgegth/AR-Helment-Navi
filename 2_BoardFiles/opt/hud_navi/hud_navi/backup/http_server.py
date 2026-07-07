"""
简易 HTTP 服务器 — 替代 BLE
手机和板子连同一个 WiFi, 手机 POST JSON 发目的地, GET 拉状态
"""
import json, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from nav_state import state

PORT = 8080

HTML_PAGE = r'''<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HUD导航</title>
<style>:root{--bg:#F2F2F7;--card:#FFF;--blue:#007AFF;--text:#1C1C1E;--sub:#8E8E93;--sep:#E5E5EA;--green:#34C759;--red:#FF3B30;--r:16px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.c{padding:16px;display:flex;flex-direction:column;gap:14px;max-width:440px;margin:0 auto}
.card{background:var(--card);border-radius:var(--r);padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.lbl{font-size:.75rem;font-weight:600;color:var(--sub);text-transform:uppercase;margin-bottom:10px}
.row{display:flex;gap:8px}
input{flex:1;height:42px;border:1.5px solid var(--sep);border-radius:12px;padding:0 14px;font-size:.95rem;background:#F9F9FB;outline:none}
.btn{width:100%;height:50px;border:none;border-radius:14px;font-size:1.05rem;font-weight:600}
.btn-b{background:var(--blue);color:#fff}.btn-b:disabled{background:#B0D0FF}
.btn-s{height:42px;padding:0 16px;background:var(--blue);color:#fff;border:none;border-radius:12px;font-size:.9rem;font-weight:600}
.btn-r{background:transparent;color:var(--red);border:1px solid var(--red);margin-top:8px}
.res{background:#F0F7FF;border:1px solid #D6EAFF;border-radius:12px;padding:12px 14px;margin-top:10px;display:none}
.np{display:none}.arrow{text-align:center;font-size:3rem;padding:8px 0}
.instr{text-align:center;padding:8px 0;font-size:1.1rem;font-weight:600;color:var(--blue)}
.stats{display:flex;justify-content:space-around;text-align:center;padding:8px 0}
.stats .v{font-size:1.5rem;font-weight:700}.stats .l{font-size:.7rem;color:var(--sub);margin-top:2px}
.toast{position:fixed;bottom:40px;left:50%;transform:translateX(-50%);background:#1C1C1E;color:#fff;padding:12px 24px;border-radius:25px;font-size:.85rem;opacity:0;transition:opacity .3s;z-index:100}
.toast.on{opacity:1}</style></head><body>
<div class="c">
<div class="card"><div class="lbl">板子地址</div><div class="row"><input id="ip" value="10.203.69.160:8080"><button class="btn-s" onclick="P()">连接</button></div><p id="sts" style="margin-top:8px;font-size:.85rem;color:var(--sub)">输入IP后点连接</p></div>
<div class="card"><div class="lbl">目的地</div><div class="row"><input id="addr" placeholder="输入地址，如天安门"><button class="btn-s" onclick="S()">搜索</button></div><div class="res" id="res"><p>📍 <span id="rname"></span></p><p style="font-size:.75rem;color:var(--sub)"><span id="rcoord"></span></p></div></div>
<div class="card np" id="nav"><div class="lbl">导航</div><div class="arrow" id="arrow">→</div><div class="instr" id="instr"></div><div class="stats"><div><div class="v" id="spd">--</div><div class="l">km/h</div></div><div><div class="v" id="rm">--</div><div class="l">剩余</div></div><div><div class="v" id="eta">--</div><div class="l">min</div></div></div></div>
<button class="btn btn-b" id="snd" onclick="D()" disabled>开始导航</button><button class="btn btn-r" id="cnl" onclick="C()" style="display:none">取消导航</button>
</div><div class="toast" id="t"></div>
<script>
var URL="http://10.203.69.160:8080", AM="6af97f35f48f772b1532efe395099ffb", DEST_LAT=null,DEST_LNG=null,DEST_NAME="", PT=null,TT;
function $(id){return document.getElementById(id)}
function T(m){var t=$("t");t.textContent=m;t.classList.add("on");clearTimeout(TT);TT=setTimeout(function(){t.classList.remove("on")},2000)}
function P(){URL=$("ip").value.trim();if(!URL.startsWith("http"))URL="http://"+URL;fetch(URL+"/ping").then(r=>r.json()).then(d=>{if(d.status=="alive"){$("sts").textContent="✅ 已连接 "+URL;T("已连接")}}).catch(()=>{$("sts").textContent="❌ 连接失败";T("失败")})}
async function S(){var a=$("addr").value.trim();if(!a){T("请输入地址");return};try{var r=await fetch("https://restapi.amap.com/v3/geocode/geo?key="+AM+"&address="+encodeURIComponent(a));var d=await r.json();if(d.status=="1"&&d.geocodes.length>0){var l=d.geocodes[0].location.split(",");DEST_LAT=parseFloat(l[1]);DEST_LNG=parseFloat(l[0]);DEST_NAME=d.geocodes[0].formatted_address;$("rname").textContent=DEST_NAME;$("rcoord").textContent=DEST_LAT.toFixed(6)+", "+DEST_LNG.toFixed(6);$("res").style.display="block";$("snd").disabled=false;T("✅ "+DEST_NAME)}else{T("未找到")}}catch(e){T("搜索失败")}}
async function D(){if(!DEST_LAT){T("请先搜索");return};try{var r=await fetch(URL+"/destination",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({lat:DEST_LAT,lon:DEST_LNG,name:DEST_NAME})});var d=await r.json();if(d.status=="ok"){T("已发送");$("snd").style.display="none";$("cnl").style.display="block";Q()}}catch(e){T("发送失败")}}
async function C(){try{await fetch(URL+"/cancel");T("已取消");$("snd").style.display="block";$("cnl").style.display="none";$("nav").style.display="none";stop()}catch(e){}}
function Q(){stop();PT=setInterval(POLL,2000);POLL()}
function stop(){if(PT){clearInterval(PT);PT=null}}
async function POLL(){try{var r=await fetch(URL+"/status");var m=await r.json();if(!m.is_navigating)return;$("nav").style.display="block";var a={left:"←",right:"→",straight:"↑",uturn:"↩",arrived:"✓"};$("arrow").textContent=a[m.instruction_direction]||"↑";$("instr").textContent=m.instruction||"";$("spd").textContent=m.speed?m.speed.toFixed(1):"--";var rm=m.remaining_distance||0;$("rm").textContent=rm>1000?(rm/1000).toFixed(1)+"km":Math.round(rm)+"m";$("eta").textContent=m.eta_minutes?Math.round(m.eta_minutes):"--"}catch(e){}}
setTimeout(P,1000);
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # 静默日志

    def do_POST(self):
        if self.path == "/destination":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            msg = json.loads(body)
            lat, lon = float(msg["lat"]), float(msg["lon"])
            name = msg.get("name", "目的地")
            with state as s:
                s.destination = (lat, lon)
                s.destination_name = name
                s.is_navigating = True
                s.is_arrived = False
                s.route = []
            print(f"收到目的地: {name} ({lat}, {lon})")
            self._ok({"status": "ok", "destination": name})
        else:
            self._ok({"status": "unknown"})

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())
        elif self.path == "/status":
            snap = state.get_snapshot()
            self._ok({
                "lat": snap.current_position[0] if snap.current_position else None,
                "lon": snap.current_position[1] if snap.current_position else None,
                "speed": snap.gps_speed,
                "remaining_distance": round(snap.remaining_distance),
                "eta_minutes": round(snap.eta_minutes),
                "instruction": snap.instruction,
                "instruction_direction": snap.turn_direction,
                "battery": round(snap.battery_level),
                "gps_signal": "weak" if snap.gps_quality["signal_weak"] else "good",
                "is_navigating": snap.is_navigating,
                "is_arrived": snap.is_arrived,
            })
        elif self.path == "/cancel":
            with state as s:
                s.is_navigating = False
                s.destination = None
                s.route = []
            self._ok({"status": "cancelled"})
        elif self.path == "/ping":
            self._ok({"status": "alive"})
        else:
            self._ok({"status": "unknown"})

    def _ok(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def start_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"HTTP 服务已启动: http://10.203.69.x:{PORT}")
    print("  POST /destination  ← 手机发目的地")
    print("  GET  /status       → 获取导航状态")
    print("  GET  /cancel       → 取消导航")
    server.serve_forever()

if __name__ == "__main__":
    start_server()
