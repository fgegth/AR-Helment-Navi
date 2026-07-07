"""
简易 HTTP 服务器 — 替代 BLE
手机和板子连同一个 WiFi, 手机 POST JSON 发目的地, GET 拉状态
"""
import json, logging, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from nav_state import state

logger = logging.getLogger("HTTP")

PORT = 8080

HTML_PAGE = r'''<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HUD 骑行导航</title>
<style>:root{--bg:#F2F2F7;--card:#FFF;--blue:#007AFF;--text:#1C1C1E;--sub:#8E8E93;--sep:#E5E5EA;--g:#34C759;--y:#FF9500;--r:#FF3B30;--rad:14px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding-bottom:60px}
.topbar{background:var(--card);padding:12px 16px;display:flex;align-items:center;gap:8px;border-bottom:0.5px solid var(--sep);position:sticky;top:0;z-index:10}
.topbar h1{font-size:1.1rem;font-weight:600;flex:1}
.topbar .dot{width:10px;height:10px;border-radius:50%;background:var(--sub)}
.topbar .dot.on{background:var(--g)}
.c{padding:12px;display:flex;flex-direction:column;gap:10px;max-width:480px;margin:0 auto}
.card{background:var(--card);border-radius:var(--rad);padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.lbl{font-size:.7rem;font-weight:600;color:var(--sub);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.row{display:flex;gap:6px;flex-wrap:wrap}
input,select{flex:1;min-width:120px;height:38px;border:1.5px solid var(--sep);border-radius:10px;padding:0 10px;font-size:.9rem;background:#F9F9FB;outline:none}
.btn{height:38px;padding:0 14px;border:none;border-radius:10px;font-size:.85rem;font-weight:600;cursor:pointer;white-space:nowrap}
.btn-blue{background:var(--blue);color:#fff}.btn-blue:disabled{background:#B0D0FF}
.btn-outline{background:transparent;color:var(--blue);border:1.5px solid var(--blue)}
.btn-red{background:transparent;color:var(--r);border:1px solid var(--r)}
.btn-sm{height:30px;padding:0 10px;font-size:.75rem;border-radius:8px;background:#F0F0F5;border:none;cursor:pointer}
.res{background:#F0F7FF;border:1px solid #D6EAFF;border-radius:10px;padding:10px 12px;margin-top:8px;display:none}
.road{display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:8px;font-size:.85rem;margin-top:6px}
.road.safe{background:#E8F8E8}.road.caution{background:#FFF8E8}.road.danger{background:#FFE8E8}
.np{display:none}.arrow{text-align:center;font-size:2.5rem;padding:4px 0}
.instr{text-align:center;font-size:.95rem;font-weight:600;color:var(--blue)}
.stats{display:flex;justify-content:space-around;text-align:center;padding:6px 0}
.stats .v{font-size:1.3rem;font-weight:700}.stats .l{font-size:.65rem;color:var(--sub);margin-top:1px}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1C1C1E;color:#fff;padding:10px 20px;border-radius:20px;font-size:.8rem;opacity:0;transition:opacity .3s;z-index:100}
.toast.on{opacity:1}
.tabs{display:flex;gap:2px;margin-bottom:4px}
.tab{flex:1;text-align:center;padding:8px;font-size:.75rem;font-weight:600;color:var(--sub);border-bottom:2px solid transparent;cursor:pointer}
.tab.on{color:var(--blue);border-bottom-color:var(--blue)}
.tab-content{display:none}
.tab-content.on{display:block}
</style></head><body>
<div class="topbar"><h1>🚲 HUD 导航</h1><div class="dot" id="dot"></div><span style="font-size:.75rem;color:var(--sub)" id="conStatus">离线</span></div>
<div class="c">
<div class="tabs"><div class="tab on" onclick="switchTab('nav')">🧭 导航</div><div class="tab" onclick="switchTab('places')">📍 常用</div><div class="tab" onclick="switchTab('history')">📊 记录</div></div>

<!-- 导航面板 -->
<div class="tab-content on" id="tab-nav">
<div class="card"><div class="lbl">搜索目的地</div><div class="row"><input id="addr" placeholder="输入地址，如天安门"><button class="btn btn-blue" onclick="S()">搜索</button></div><div class="res" id="res"><div>📍 <span id="rname"></span></div><div style="font-size:.7rem;color:var(--sub);margin-top:2px"><span id="rcoord"></span></div></div></div>
<div class="card np" id="navCard"><div class="lbl" id="roadLbl" style="color:var(--y)">🟡 当前道路</div><div class="arrow" id="arrow">↑</div><div class="instr" id="instr">--</div><div class="road" id="roadBar" style="display:none"></div><div class="stats"><div><div class="v" id="spd">--</div><div class="l">km/h</div></div><div><div class="v" id="rm">--</div><div class="l">剩余</div></div><div><div class="v" id="eta">--</div><div class="l">min</div></div></div></div>
<button class="btn btn-blue" id="snd" onclick="D()" disabled style="width:100%;height:44px">▶ 开始导航</button>
<button class="btn btn-red" id="cnl" onclick="C()" style="display:none;width:100%;height:44px">■ 取消导航</button>
<div class="row" style="margin-top:6px"><button class="btn btn-sm" onclick="quickNav('公司')">🏢 公司</button><button class="btn btn-sm" onclick="quickNav('家')">🏠 回家</button><button class="btn btn-sm" onclick="quickNav('天安门')">🗺 天安门</button></div>
</div>

<!-- 常用地点 -->
<div class="tab-content" id="tab-places"><div class="card"><div class="lbl">常用地点</div><div id="placesList" style="font-size:.85rem;color:var(--sub)">加载中...</div></div></div>

<!-- 历史记录 -->
<div class="tab-content" id="tab-history"><div class="card"><div class="lbl">骑行统计</div><div id="statsBox" style="font-size:.85rem">加载中...</div></div></div>
</div>
<div class="toast" id="toast"></div>
<script>
var URL="http://"+location.host, AM="6af97f35f48f772b1532efe395099ffb", DEST_LAT=null,DEST_LNG=null,DEST_NAME="", PT=null,TT;
var arrows={left:"←",right:"→",straight:"↑",uturn:"↩",arrived:"✓"};
function _(id){return document.getElementById(id)}
function toast(m){var t=_("toast");t.textContent=m;t.classList.add("on");clearTimeout(TT);TT=setTimeout(function(){t.classList.remove("on")},2000)}
function switchTab(n){document.querySelectorAll(".tab,.tab-content").forEach(function(e){e.classList.remove("on")});event.target.classList.add("on");_("tab-"+n).classList.add("on");if(n=="places")loadPlaces();if(n=="history")loadStats()}
async function ping(){try{var r=await fetch(URL+"/ping");if((await r.json()).status=="alive"){_("dot").className="dot on";_("conStatus").textContent="已连接"}else{_("dot").className="dot";_("conStatus").textContent="离线"}}catch(e){_("dot").className="dot";_("conStatus").textContent="离线"}}
async function S(){var a=_("addr").value.trim();if(!a){toast("请输入地址");return};try{var r=await fetch("https://restapi.amap.com/v3/geocode/geo?key="+AM+"&address="+encodeURIComponent(a));var d=await r.json();if(d.status=="1"&&d.geocodes.length>0){var l=d.geocodes[0].location.split(",");DEST_LAT=parseFloat(l[1]);DEST_LNG=parseFloat(l[0]);DEST_NAME=d.geocodes[0].formatted_address;_("rname").textContent=DEST_NAME;_("rcoord").textContent=DEST_LAT.toFixed(6)+", "+DEST_LNG.toFixed(6);_("res").style.display="block";_("snd").disabled=false;toast("✅ "+DEST_NAME)}else{toast("未找到")}}catch(e){toast("搜索失败")}}
async function D(){if(!DEST_LAT){toast("请先搜索");return};try{var r=await fetch(URL+"/destination",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({lat:DEST_LAT,lon:DEST_LNG,name:DEST_NAME})});if((await r.json()).status=="ok"){toast("已发送");_("snd").style.display="none";_("cnl").style.display="block";pollStart()}}catch(e){toast("发送失败")}}
async function C(){try{await fetch(URL+"/cancel");toast("已取消");_("snd").style.display="block";_("cnl").style.display="none";_("navCard").style.display="none";stop()}catch(e){}}
function pollStart(){stop();PT=setInterval(poll,2000);poll()}
function stop(){if(PT){clearInterval(PT);PT=null}}
async function poll(){try{var r=await fetch(URL+"/status");var m=await r.json();if(!m.is_navigating)return;_("navCard").style.display="block";_("arrow").textContent=arrows[m.instruction_direction]||"↑";_("instr").textContent=m.instruction||"";_("spd").textContent=m.speed?m.speed.toFixed(1):"--";var rm=m.remaining_distance||0;_("rm").textContent=rm>1000?(rm/1000).toFixed(1)+"km":Math.round(rm)+"m";_("eta").textContent=m.eta_minutes?Math.round(m.eta_minutes):"--";if(m.road_condition){_("roadLbl").textContent=m.road_condition}_("roadLbl").style.color=m.road_summary&&m.road_summary.includes("🔴")?"var(--r)":m.road_summary&&m.road_summary.includes("🟡")?"var(--y)":"var(--g)"}catch(e){}}
async function quickNav(name){var r=await fetch(URL+"/places");var d=await r.json();var found=null;(d.places||[]).forEach(function(p){if(p.name.indexOf(name)>=0)found=p});if(found){DEST_LAT=found.lat;DEST_LNG=found.lon;DEST_NAME=found.name;_("rname").textContent=found.name;_("rcoord").textContent=found.lat.toFixed(4)+","+found.lon.toFixed(4);_("res").style.display="block";_("snd").disabled=false;toast("📍 "+found.name)}else{toast("未找到常用地点，请先搜索")}}
async function loadPlaces(){try{var r=await fetch(URL+"/places");var d=await r.json();var h="";(d.places||[]).forEach(function(p){h+="<div style='padding:8px 0;border-bottom:0.5px solid var(--sep);cursor:pointer' onclick=\"DEST_LAT="+p.lat+";DEST_LNG="+p.lon+";DEST_NAME='"+p.name+"';_('rname').textContent='"+p.name+"';_('rcoord').textContent='"+p.lat.toFixed(4)+","+p.lon.toFixed(4)+"';_('res').style.display='block';_('snd').disabled=false;switchTab('nav')\">📍 <b>"+p.name+"</b> <span style='color:var(--sub);font-size:.75rem'>"+p.count+"次</span></div>"});_("placesList").innerHTML=h||"暂无常用地点"}catch(e){_("placesList").innerHTML="加载失败"}}
async function loadStats(){try{var r=await fetch(URL+"/stats");var d=await r.json();_("statsBox").innerHTML="<div class='stats'><div><div class='v'>"+d.total_rides+"</div><div class='l'>总次数</div></div><div><div class='v'>"+d.total_km+"</div><div class='l'>总里程km</div></div><div><div class='v'>"+Math.round(d.total_min)+"</div><div class='l'>总时长min</div></div></div>"+(d.last_ride?"<div style='margin-top:8px;font-size:.75rem;color:var(--sub)'>最近: "+d.last_ride.date+" "+d.last_ride.time+" | "+d.last_ride.distance_km+"km | "+d.last_ride.destination+"</div>":"")}catch(e){_("statsBox").innerHTML="加载失败"}}
setInterval(ping,5000);ping();
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # 静默日志

    def do_POST(self):
        if self.path == "/destination":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                msg = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._ok({"status": "error", "msg": "JSON格式错误"})
                return
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
        elif self.path == "/places/rename":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                msg = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._ok({"status": "error", "msg": "JSON格式错误"})
                return
            from smart_features import rename_place
            old = msg.get("old_name", "")
            new = msg.get("new_name", "")
            if old and new:
                rename_place(old, new)
                self._ok({"status": "renamed", "old": old, "new": new})
            else:
                self._ok({"status": "error", "msg": "缺少参数"})
        elif self.path == "/places/add":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                msg = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._ok({"status": "error", "msg": "JSON格式错误"})
                return
            from smart_features import record_destination
            lat, lon = float(msg.get("lat", 0)), float(msg.get("lon", 0))
            name = msg.get("name", "收藏地点")
            record_destination(name, lat, lon)
            self._ok({"status": "added", "name": name})
        else:
            self._ok({"status": "unknown"})

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                with open("/opt/hud_navi/phone_final.html", "r", encoding="utf-8") as ff:
                    self.wfile.write(ff.read().encode())
            except:
                self.wfile.write(HTML_PAGE.encode())
        elif self.path.startswith("/map"):
            try:
                with open("/opt/hud_navi/map_nav.html", "r", encoding="utf-8") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())
            except:
                self.send_response(404)
                self.end_headers()
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
                "road_condition": snap.road_condition,
                "road_summary": snap.road_summary,
                "voice_last_cmd": snap.voice_last_cmd,
                "voice_last_raw": snap.voice_last_raw,
                "voice_last_time": snap.voice_last_time,
            })
        elif self.path == "/cancel":
            with state as s:
                s.is_navigating = False
                s.is_arrived = False
                s.destination = None
                s.route = []
            self._ok({"status": "cancelled"})
        elif self.path == "/places":
            from smart_features import get_frequent_places
            self._ok({"places": get_frequent_places()})
        elif self.path == "/stats":
            from smart_features import get_weekly_stats
            self._ok(get_weekly_stats())
        elif self.path == "/rides":
            import os, glob as _g
            rides = []
            for f in sorted(_g.glob("/opt/hud_navi/data/rides/*.json"), reverse=True)[:10]:
                with open(f) as fp:
                    rides.append(json.load(fp))
            self._ok({"rides": rides})
        elif self.path == "/analysis":
            from ride_analysis import get_detailed_stats, get_speed_distribution, get_monthly_trend
            self._ok({
                "stats": get_detailed_stats(),
                "speed_dist": get_speed_distribution(),
                "monthly": get_monthly_trend(),
            })
        elif self.path == "/ai/status":
            from ai_alert import get_alert_status
            self._ok(get_alert_status())
        elif self.path == "/ai/start":
            # AI检测暂未启用, 功能预留
            self._ok({"status": "disabled", "msg": "AI检测功能暂未启用"})
        elif self.path == "/ping":
            self._ok({"status": "alive"})
        elif self.path == "/restart":
            import subprocess as _sp
            _sp.Popen(["reboot"], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            self._ok({"status": "rebooting"})
        elif self.path == "/shutdown":
            import subprocess as _sp
            _sp.Popen(["poweroff"], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            self._ok({"status": "shutting_down"})
        elif self.path == "/voice/enroll":
            with _enroll_lock:
                if _enroll_result["status"] == "enrolling":
                    self._ok({"status": "busy", "msg": "正在录入中,请稍后"})
                    return
                _enroll_result["status"] = "enrolling"
                _enroll_result["msg"] = "录音中..."
            def _do_enroll():
                try:
                    from voice_auth import enroll
                    ok = enroll("owner", 4)
                    with _enroll_lock:
                        if ok:
                            _enroll_result.update({"status": "ok", "msg": "声纹录入成功", "time": _time.time()})
                            logger.info("声纹录入: 成功")
                        else:
                            _enroll_result.update({"status": "fail", "msg": "未检测到人声", "time": _time.time()})
                            logger.warning("声纹录入: 未检测到人声")
                except Exception as e:
                    with _enroll_lock:
                        _enroll_result.update({"status": "fail", "msg": "录入异常: %s" % e, "time": _time.time()})
                    logger.error("声纹录入异常: %s", e)
            threading.Thread(target=_do_enroll, daemon=True).start()
            self._ok({"status": "enrolling", "msg": "请对麦克风说话"})
        elif self.path == "/voice/cmd_enroll":
            with _enroll_lock:
                if _enroll_result["status"] == "enrolling":
                    self._ok({"status": "busy", "msg": "正在录入中,请稍后"})
                    return
                _enroll_result["status"] = "enrolling"
                _enroll_result["msg"] = "命令录制中..."
            def _do_cmd_enroll():
                try:
                    from voice_command import enroll_all
                    enroll_all()
                    with _enroll_lock:
                        _enroll_result.update({"status": "ok", "msg": "命令录入完成", "time": _time.time()})
                    logger.info("命令录入完成")
                except Exception as e:
                    with _enroll_lock:
                        _enroll_result.update({"status": "fail", "msg": "命令录入异常: %s" % e, "time": _time.time()})
                    logger.error("命令录入异常: %s", e)
            threading.Thread(target=_do_cmd_enroll, daemon=True).start()
            self._ok({"status": "enrolling", "msg": "请依次说5个命令词"})
        elif self.path == "/voice/status":
            import os as _os, json as _json
            vp = 0
            if _os.path.exists("/opt/hud_navi/data/voiceprint.json"):
                try:
                    d = _json.load(open("/opt/hud_navi/data/voiceprint.json"))
                    vp = d.get("_count", len(d) - 1 if "_count" in d else len(d))
                except: pass  # 文件写入中, 用上次的值
            cmds = 0
            if _os.path.exists("/opt/hud_navi/data/commands.json"):
                try:
                    cd = _json.load(open("/opt/hud_navi/data/commands.json"))
                    cmds = cd.get("_count", len(cd) - 1 if "_count" in cd else len(cd))
                except: pass  # 文件写入中, 用上次的值
            wavs = len([f for f in _os.listdir("/opt/hud_navi/data/voice_prompts") if f.endswith('.wav')]) if _os.path.exists("/opt/hud_navi/data/voice_prompts") else 0
            with _enroll_lock:
                er = dict(_enroll_result)
            self._ok({"voiceprints": vp, "commands": cmds, "wav_prompts": wavs, "enroll_result": er})
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

import threading, time as _time

# 录入状态追踪（异步模式：HTTP立即返回，后台录音，APK轮询/voice/status获取结果）
_enroll_lock = threading.Lock()
_enroll_result = {"status": "idle", "msg": "", "time": 0}

def start_server():
    import socket as _sock, subprocess as _sp, time as _time
    # 自动清理占用端口的旧进程（兼容BusyBox无fuser -k的情况）
    for attempt in range(3):
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        if s.connect_ex(("127.0.0.1", PORT)) != 0:
            s.close()
            break  # 端口空闲
        s.close()
        # 尝试多种方式清理端口
        try:
            _sp.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True, timeout=3)
        except Exception:
            pass
        try:
            _sp.run("kill $(netstat -tlnp 2>/dev/null | grep :%d | awk '{print $NF}' | cut -d/ -f1)" % PORT,
                    shell=True, capture_output=True)
        except Exception:
            pass
        _time.sleep(1)

    class _Reuse(HTTPServer):
        allow_reuse_address = True
        def server_bind(self):
            self.socket.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
            super().server_bind()
    server = _Reuse(("0.0.0.0", PORT), Handler)
    print(f"HTTP 服务已启动: http://10.203.69.x:{PORT}")
    print("  POST /destination  ← 手机发目的地")
    print("  GET  /status       → 获取导航状态")
    print("  GET  /cancel       → 取消导航")
    server.serve_forever()

if __name__ == "__main__":
    start_server()
