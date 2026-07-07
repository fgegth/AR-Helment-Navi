"""
WiFi 热点配网 — hostapd + dnsmasq + Python HTTP
开机无 WiFi → 开免密热点 "HUD-Setup" → 手机连上 192.168.4.1 输密码 → 自动联网
"""
import subprocess, os, json, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

AP_SSID = "HUD-Setup"
AP_IFACE = "wlan0"
AP_IP = "192.168.4.1"
SAVED = "/opt/hud_navi/data/wifi_saved.json"
PROMPT_DIR = "/opt/hud_navi/data/voice_prompts"

CAPTIVE = """<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HUD WiFi</title>
<style>:root{--bg:#F2F2F7;--card:#FFF;--blue:#007AFF;--text:#1C1C1E;--sub:#8E8E93}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.c{background:var(--card);border-radius:18px;padding:24px;width:100%;max-width:360px;box-shadow:0 4px 20px rgba(0,0,0,.08)}
h2{font-size:1.3rem;font-weight:700;text-align:center;margin-bottom:4px}
.sub{font-size:.75rem;color:var(--sub);text-align:center;margin-bottom:20px}
label{font-size:.7rem;font-weight:600;color:var(--sub);text-transform:uppercase;margin-bottom:4px;display:block}
input{width:100%;height:42px;border:1.5px solid #E5E5EA;border-radius:12px;padding:0 12px;font-size:.9rem;margin-bottom:14px;background:#F9F9FB;outline:none}
input:focus{border-color:var(--blue)}
button{width:100%;height:46px;background:var(--blue);color:#fff;border:none;border-radius:12px;font-size:1rem;font-weight:600;cursor:pointer}
button:active{opacity:.8}
.msg{text-align:center;font-size:.8rem;margin-top:12px;color:var(--sub)}.msg.ok{color:#34C759}.msg.err{color:#FF3B30}
</style></head><body>
<div class="c"><h2>🚲 HUD 导航配网</h2><div class="sub">首次使用需配置 WiFi，以后开机自动连接</div>
<label>WiFi 名称</label><input id="s" placeholder="如: Xiaomi 14" autocomplete="off">
<label>WiFi 密码</label><input id="p" type="password" placeholder="输入密码">
<button onclick="C()">连接</button><div class="msg" id="m"></div><div style="text-align:center;font-size:.7rem;color:var(--sub);margin-top:16px;line-height:1.6">⚡ 仅需配置一次<br>以后开机自动连接 WiFi</div></div>
<script>
async function C(){
var s=document.getElementById('s').value.trim(),p=document.getElementById('p').value.trim(),m=document.getElementById('m');
if(!s){m.className='msg err';m.textContent='请输入WiFi名称';return}
m.className='msg';m.textContent='连接中...';
try{var r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ssid:s,psk:p})});var d=await r.json();
m.className=d.status=='ok'?'msg ok':'msg err';m.textContent=d.status=='ok'?'✅ 已收到！板子将在3秒后重启...':'❌ '+d.msg}catch(e){m.className='msg err';m.textContent='网络错误'}
}
</script></div></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        # 所有请求都返回配网页面（强制门户）
        self.send_response(200);self.send_header("Content-Type","text/html;charset=utf-8");self.end_headers();self.wfile.write(CAPTIVE.encode())
    def do_POST(self):
        if self.path=="/save":
            d=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            ssid=d.get("ssid","").strip(); psk=d.get("psk","").strip()
            if ssid:
                os.makedirs(os.path.dirname(SAVED),exist_ok=True)
                with open(SAVED,"w") as f: json.dump({"ssid":ssid,"psk":psk},f)
                self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers();self.wfile.write(b'{"status":"ok","msg":"WiFi saved, rebooting..."}')
                threading.Thread(target=_switch,args=(ssid,psk),daemon=True).start()
            else:
                self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers();self.wfile.write(b'{"status":"err","msg":"SSID empty"}')

def _run(cmd):
    try: return subprocess.run(cmd,shell=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5).returncode
    except: return -1

def _start_ap():
    """hostapd + dnsmasq 专业 AP 热点"""
    # hostapd 配置
    with open("/tmp/hostapd.conf","w") as f:
        f.write(f"interface={AP_IFACE}\ndriver=nl80211\nssid={AP_SSID}\nhw_mode=g\nchannel=6\nauth_algs=1\nwmm_enabled=0\n")
    _run("killall hostapd dnsmasq 2>/dev/null; sleep 1")
    _run(f"ifconfig {AP_IFACE} {AP_IP} netmask 255.255.255.0 up")
    _run("hostapd -B /tmp/hostapd.conf")
    # dnsmasq DHCP
    with open("/tmp/dnsmasq.conf","w") as f:
        f.write(f"interface={AP_IFACE}\ndhcp-range=192.168.4.2,192.168.4.100,12h\ndhcp-option=3,{AP_IP}\nport=0\naddress=/#/{AP_IP}\n")
    _run("dnsmasq -C /tmp/dnsmasq.conf")
    print(f"AP 已开启: {AP_SSID} (免密码) → http://{AP_IP}")

def _stop_ap():
    _run("killall hostapd dnsmasq 2>/dev/null")
    _run(f"ifconfig {AP_IFACE} 0.0.0.0")

def _switch(ssid, psk):
    """保存后重连 WiFi，然后优雅退出配网模式"""
    time.sleep(2)
    _stop_ap()
    conf = "/tmp/wifi_client.conf"
    _run(f"wpa_passphrase '{ssid}' '{psk}' > {conf}")
    _run(f"wpa_supplicant -D nl80211 -i {AP_IFACE} -c {conf} -B")
    time.sleep(4)
    # DHCP 获取IP (带重试)
    for _ in range(3):
        _run(f"udhcpc -i {AP_IFACE} -t 5 -n")
        time.sleep(2)
        r = subprocess.run(f"ifconfig {AP_IFACE} | grep 'inet '", shell=True, capture_output=True)
        if r.returncode == 0:
            break
    print(f"WiFi 已连接: {ssid}，配网完成")
    # 向HTTP服务器发送停止信号
    import sys
    sys.exit(0)

def _connect_saved():
    """尝试连接已保存的WiFi, 带重试逻辑 (最多3次, 间隔5秒)"""
    if not os.path.exists(SAVED):
        return False
    with open(SAVED) as f:
        d = json.load(f)

    max_retries = 3
    retry_delay = 5  # 秒

    for attempt in range(1, max_retries + 1):
        print(f"WiFi连接尝试 {attempt}/{max_retries}: {d['ssid']}")
        _run("killall wpa_supplicant 2>/dev/null; sleep 1")
        _run(f"wpa_passphrase '{d['ssid']}' '{d['psk']}' > /tmp/wifi_client.conf")
        _run(f"wpa_supplicant -D nl80211 -i {AP_IFACE} -c /tmp/wifi_client.conf -B")
        time.sleep(3)

        # 尝试 DHCP 获取 IP
        for dhcp_attempt in range(3):
            _run(f"udhcpc -i {AP_IFACE} -t 5 -n")
            time.sleep(2)
            r = subprocess.run(
                f"ifconfig {AP_IFACE} | grep 'inet '",
                shell=True, capture_output=True
            )
            if r.returncode == 0:
                print(f"WiFi 已连接: {d['ssid']} (尝试{attempt}次)")
                return True
            print(f"  DHCP 尝试 {dhcp_attempt + 1} 失败, 重试...")

        if attempt < max_retries:
            print(f"  连接失败, {retry_delay}秒后重试...")
            time.sleep(retry_delay)

    print(f"WiFi连接失败: 已重试{max_retries}次, 回退到AP模式")
    return False


def run_setup():
    # 等待WiFi模块就绪
    print("等待WiFi模块就绪...")
    time.sleep(3)

    # 1. 尝试已保存 WiFi (带重试)
    if _connect_saved():
        print("WiFi 已连接 (已保存)")
        return True

    # 2. 全部重试失败 → 开热点配网
    print("所有WiFi连接尝试失败, 开启配网热点")
    _start_ap()
    try:
        server = HTTPServer(("0.0.0.0", 80), Handler)
        print(f"配网服务器: http://{AP_IP}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return False

if __name__=="__main__":
    run_setup()
