"""
音频设备管理器 — 自动检测 + 优先级切换 + 手动覆盖

优先级 (从高到低):
  1. 有线耳机 (USB声卡 或 3.5mm检测)
  2. 蓝牙耳机 (BlueZ A2DP/HFP)
  3. 开发板内置 (rk809-codec)

检测方式:
  - 有线: /proc/asound/cards 中 card1+ 出现即视为USB声卡插入
  - 蓝牙: bluetoothctl info 检查已连接音频设备
  - 板载: 始终存在

用法:
  mgr = AudioManager()
  mgr.scan_devices()          # 扫描所有设备
  mgr.get_best_input()        # 获取最佳麦克风设备 (如 "hw:1,0")
  mgr.get_best_output()       # 获取最佳扬声器设备
  mgr.set_override("bt")      # 手动切换: "wired" / "bt" / "board"
  mgr.set_override("auto")    # 恢复自动检测
"""

import os
import re
import subprocess
import logging
import threading
import time

logger = logging.getLogger(__name__)


# ============================================================
# 设备类型
# ============================================================
class AudioDevice:
    def __init__(self, card_id: int, name: str, dev_type: str,
                 pcm_capture: str = None, pcm_playback: str = None):
        self.card_id = card_id
        self.name = name
        self.dev_type = dev_type    # "board" / "wired" / "bt"
        self.pcm_capture = pcm_capture   # e.g. "hw:1,0"
        self.pcm_playback = pcm_playback # e.g. "hw:1,0"

    def __repr__(self):
        return f"AudioDevice({self.dev_type}: {self.name})"


# ============================================================
# 检测引擎
# ============================================================

class AudioManager:
    """单例音频管理器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._override = "auto"  # "auto" / "wired" / "bt" / "board"
        self._devices = []       # 所有检测到的设备
        self._active_input = "hw:0,0"
        self._active_output = "hw:0,0"
        self._active_type = "board"
        self._last_scan = 0
        self._scan_interval = 3  # 最小扫描间隔(秒)

    # ---- 扫描 ----

    def scan_devices(self, force: bool = False) -> list:
        """扫描所有音频设备, 返回设备列表"""
        now = time.time()
        if not force and now - self._last_scan < self._scan_interval and self._devices:
            return self._devices
        self._last_scan = now

        devices = []

        # 1. 从 /proc/asound/cards 解析所有 ALSA 声卡
        alsa_cards = self._scan_alsa_cards()
        for card_id, name in alsa_cards:
            # 确定类型
            if card_id == 0:
                dev_type = "board"
            elif self._is_bluetooth_card(name):
                dev_type = "bt"
            else:
                dev_type = "wired"

            # 查找 PCM 设备 (USB 声卡可能设备号非0)
            capture = self._find_pcm(card_id, "capture")
            playback = self._find_pcm(card_id, "playback")

            if capture or playback:
                devices.append(AudioDevice(
                    card_id=card_id,
                    name=name,
                    dev_type=dev_type,
                    pcm_capture=capture,
                    pcm_playback=playback,
                ))

        # 2. 补充蓝牙检测
        bt_devices = self._scan_bluetooth()
        for bt in bt_devices:
            if not any(d.dev_type == "bt" and d.name == bt["name"] for d in devices):
                devices.append(AudioDevice(
                    card_id=-1,
                    name=bt["name"],
                    dev_type="bt",
                    pcm_capture=bt.get("capture", "bluealsa"),
                    pcm_playback=bt.get("playback", "bluealsa"),
                ))

        # 3. 检测变化并日志
        old_names = [(d.card_id, d.name) for d in self._devices]
        new_names = [(d.card_id, d.name) for d in devices]

        with self._lock:
            self._devices = devices
            self._update_active()

        if old_names != new_names:
            logger.info("音频设备变化: %s -> %s (当前: %s)",
                        [n for _, n in old_names],
                        [n for _, n in new_names],
                        self._active_type)
        else:
            logger.debug("音频扫描: %d 个设备 -> 当前: %s (%s)",
                         len(devices), self._active_type, self._active_output)

        return devices

    def _scan_alsa_cards(self) -> list:
        """解析 /proc/asound/cards, 返回 [(card_id, name), ...]"""
        cards = []
        try:
            with open("/proc/asound/cards", "r") as f:
                content = f.read()
            # 格式: " 0 [rockchiprk809co]: ..."
            for match in re.finditer(r'^\s*(\d+)\s*\[(\w+)\]\s*:', content, re.MULTILINE):
                card_id = int(match.group(1))
                name = match.group(2)
                cards.append((card_id, name))
        except Exception as e:
            logger.debug(f"读取 /proc/asound/cards 失败: {e}")
        return cards

    def _is_bluetooth_card(self, name: str) -> bool:
        """判断声卡是否为蓝牙设备"""
        bt_keywords = ["bluetooth", "bluez", "blue", "bt", "a2dp", "hfp", "hsp", "sco"]
        name_lower = name.lower()
        return any(kw in name_lower for kw in bt_keywords)

    def _find_pcm(self, card_id: int, direction: str) -> str:
        """查找声卡的 PCM 设备号 (兼容 USB 声卡)"""
        suffix = "c" if direction == "capture" else "p"
        dev_dir = f"/sys/class/sound/card{card_id}"

        # 方法1: 从 /sys/class/sound/ 查找
        try:
            for entry in os.listdir(dev_dir):
                if entry.startswith("pcmC") and entry.endswith(suffix):
                    match = re.search(r'pcmC\d+D(\d+)' + suffix, entry)
                    if match:
                        return f"hw:{card_id},{match.group(1)}"
        except Exception:
            pass

        # 方法2: 用 arecord/aplay 查询
        try:
            if direction == "capture":
                result = subprocess.run(
                    ["arecord", "-l"], capture_output=True, timeout=2, text=True,
                )
            else:
                result = subprocess.run(
                    ["aplay", "-l"], capture_output=True, timeout=2, text=True,
                )
            # 寻找 "card {id}: ... device {n}:"
            pattern = rf'card\s+{card_id}\s*:.*?device\s+(\d+)\s*:'
            match = re.search(pattern, result.stdout, re.DOTALL)
            if match:
                return f"hw:{card_id},{match.group(1)}"
        except Exception:
            pass

        # 方法3: 默认设备 0 (板载声卡总是 device 0)
        return f"hw:{card_id},0"

    def _scan_bluetooth(self) -> list:
        """通过 BlueZ 检测已连接的蓝牙音频设备 (超时保护)"""
        devices = []
        try:
            # 检查 bluetoothd 是否运行
            if not os.path.exists("/var/run/dbus/system_bus_socket"):
                return devices
            # 用 bluetoothctl devices 列出已配对设备
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True, timeout=3, text=True,
            )
            for line in result.stdout.split("\n"):
                # "Device XX:XX:XX:XX:XX:XX DeviceName"
                match = re.match(r'Device\s+([0-9A-F:]{17})\s+(.+)', line.strip())
                if not match:
                    continue
                addr, name = match.group(1), match.group(2).strip()

                # 检查是否已连接 (短超时, 避免阻塞)
                info = subprocess.run(
                    ["bluetoothctl", "info", addr],
                    capture_output=True, timeout=2, text=True,
                )
                if "Connected: yes" not in info.stdout:
                    continue

                # 检查是否支持音频 (A2DP/HFP)
                if any(x in info.stdout for x in ["Audio Source", "Audio Sink", "Headset", "A2DP", "HFP"]):
                    devices.append({
                        "name": name,
                        "addr": addr,
                        "capture": "bluealsa",
                        "playback": "bluealsa",
                    })
        except Exception as e:
            logger.debug(f"蓝牙扫描跳过: {e}")
        return devices

    # ---- 3.5mm 耳机插孔检测 (GPIO) ----
    def check_headphone_jack(self) -> bool:
        """检测 3.5mm 耳机是否插入 (GPIO 读取)"""
        # RK809 芯片的耳机检测通常在 /sys/ 或通过 ALSA mixer
        # 常见路径:
        paths = [
            "/sys/kernel/headset/state",
            "/sys/class/switch/h2w/state",
            "/sys/class/extcon/extcon0/state",
            "/sys/devices/platform/rockchip-sound/sound/card0/jack",
        ]
        for p in paths:
            try:
                with open(p, "r") as f:
                    val = f.read().strip()
                    if val in ("1", "true", "on", "connected"):
                        return True
            except Exception:
                continue

        # 通过 amixer 检测 Headphone Jack
        try:
            result = subprocess.run(
                ["amixer", "-c", "0", "contents"],
                capture_output=True, timeout=3, text=True,
            )
            for line in result.stdout.split("\n"):
                if "Jack Sense" in line or "Headphone" in line:
                    if "values=on" in line.lower():
                        return True
        except Exception:
            pass

        return False

    # ---- 活动设备选择 ----

    @property
    def override(self) -> str:
        return self._override

    def set_override(self, mode: str):
        """手动切换音频设备: "auto" / "wired" / "bt" / "board" """
        valid = ["auto", "wired", "bt", "board"]
        if mode not in valid:
            logger.warning("无效的音频模式: %s", mode)
            return
        with self._lock:
            self._override = mode
        logger.info("音频模式切换: %s", mode)

        # 切换麦克风路径
        if mode == "wired":
            # 有线耳机 -> 尝试使用耳麦 (Hands Free Mic)
            if not self.set_mic_path("Hands Free Mic"):
                self.set_mic_path("Main Mic")  # 降级
        elif mode == "board":
            self.set_mic_path("Main Mic")
        elif mode == "bt":
            self.set_mic_path("BT Sco Mic")

        self.scan_devices(force=True)  # 强制重新扫描

    def get_status(self) -> dict:
        """获取当前音频状态 (供 HTTP API / Web UI 使用)"""
        self.scan_devices()
        devices_info = []
        for d in self._devices:
            devices_info.append({
                "card_id": d.card_id,
                "name": d.name,
                "type": d.dev_type,
                "active": d.dev_type == self._active_type,
                "capture": d.pcm_capture,
                "playback": d.pcm_playback,
            })
        return {
            "active_type": self._active_type,
            "active_input": self._active_input,
            "active_output": self._active_output,
            "override": self._override,
            "headphone_jack": self.check_headphone_jack(),
            "devices": devices_info,
        }

    def _update_active(self):
        """根据 override 和优先级更新活动设备"""
        if not self._devices:
            self._active_type = "board"
            self._active_input = "hw:0,0"
            self._active_output = "hw:0,0"
            return

        # 手动覆盖
        if self._override != "auto":
            for d in self._devices:
                if d.dev_type == self._override:
                    self._active_type = d.dev_type
                    self._active_input = d.pcm_capture or f"hw:{d.card_id},0"
                    self._active_output = d.pcm_playback or f"hw:{d.card_id},0"
                    return
            logger.warning(f"手动模式 '{self._override}' 无可用设备, 降级为自动")

        # 自动优先级: wired > bt > board
        priority = ["wired", "bt", "board"]
        for p in priority:
            for d in self._devices:
                if d.dev_type == p:
                    self._active_type = d.dev_type
                    self._active_input = d.pcm_capture or f"hw:{d.card_id},0"
                    self._active_output = d.pcm_playback or f"hw:{d.card_id},0"
                    return

    def get_best_input(self) -> str:
        """获取最佳录音设备 (e.g. 'hw:1,0')"""
        self.scan_devices()
        return self._active_input

    def get_best_output(self) -> str:
        """获取最佳播放设备 (e.g. 'hw:1,0')"""
        self.scan_devices()
        return self._active_output

    def get_env(self) -> dict:
        """返回应该设置的环境变量"""
        inp = self.get_best_input()
        out = self.get_best_output()
        return {
            "AUDIODEV": out,
            "AUDIO_INPUT_DEV": inp,
            "AUDIO_OUTPUT_DEV": out,
        }

    # ---- 后台监控线程 ----

    def start_monitor(self, interval: float = 5.0):
        """启动后台设备热插拔监控"""
        def _monitor():
            last_devices = []
            while True:
                time.sleep(interval)
                current = self.scan_devices()
                current_ids = [(d.card_id, d.dev_type) for d in current]
                if current_ids != last_devices:
                    logger.info(f"音频设备变化: {len(last_devices)} -> {len(current_ids)}")
                    last_devices = current_ids

        t = threading.Thread(target=_monitor, daemon=True, name="AudioMon")
        t.start()
        logger.info("音频热插拔监控已启动")

    # ---- Mixer 控制 (RK809 等 codec) ----

    def _get_mic_paths(self) -> list:
        """获取可用的麦克风路径列表"""
        paths = []
        try:
            # 获取 Capture MIC Path 的可选项
            result = subprocess.run(
                ["amixer", "-c", "0", "cget", "numid=2"],
                capture_output=True, timeout=2, text=True,
            )
            for line in result.stdout.split("\n"):
                match = re.search(r"Item #(\d+)\s+'(.+)'", line)
                if match:
                    paths.append((int(match.group(1)), match.group(2)))
        except Exception:
            pass
        return paths

    def set_mic_path(self, path_name: str):
        """设置麦克风输入路径: 'Main Mic' / 'Hands Free Mic' / 'MIC OFF'"""
        paths = self._get_mic_paths()
        for num, name in paths:
            if path_name.lower() in name.lower():
                try:
                    subprocess.run(
                        ["amixer", "-c", "0", "cset", "numid=2", str(num)],
                        capture_output=True, timeout=2,
                    )
                    logger.info("麦克风切换: %s (numid=2, val=%d)", name, num)
                    return True
                except Exception as e:
                    logger.warning("切换麦克风失败: %s", e)
        return False

    def init_mixer(self):
        """初始化音频 mixer (启动时调用)"""
        # 1. 打开麦克风 (默认用板载主麦克风)
        self.set_mic_path("Main Mic")
        # 2. 确保耳机音量正常
        try:
            subprocess.run(
                ["amixer", "-c", "0", "cset", "numid=3", "255"],
                capture_output=True, timeout=2,
            )
            subprocess.run(
                ["amixer", "-c", "0", "cset", "numid=4", "255"],
                capture_output=True, timeout=2,
            )
        except Exception:
            pass
        logger.info("Mixer 初始化完成: 麦克风=Main Mic, 耳机音量=255")

    # ---- 环境变量 ----

    def apply_env(self):
        """将当前最佳设备写入 os.environ"""
        env = self.get_env()
        for key, val in env.items():
            os.environ[key] = val
        logger.debug(f"音频环境: AUDIODEV={env['AUDIODEV']}")


# ============================================================
# 全局单例
# ============================================================

audio_manager = AudioManager()


def get_input_device() -> str:
    """获取当前最佳录音设备 (便捷函数)"""
    return audio_manager.get_best_input()


def get_output_device() -> str:
    """获取当前最佳播放设备 (便捷函数, 自动使用 plughw 做格式转换)"""
    dev = audio_manager.get_best_output()
    # rk809 codec 只支持特定格式 (如立体声), plughw 自动转换采样率/通道数
    if dev and dev.startswith("hw:"):
        return dev.replace("hw:", "plughw:", 1)
    return dev or "plughw:0,0"


# ============================================================
# 命令行测试
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mgr = AudioManager()
    status = mgr.get_status()
    print("Audio Devices:")
    for d in status["devices"]:
        mark = " <-- active" if d["active"] else ""
        print(f"  [{d['type']:>5}] card{d['card_id']}: {d['name']}{mark}")
    print(f"\nActive: {status['active_type']} -> {status['active_input']} / {status['active_output']}")
    print(f"Headphone jack: {status['headphone_jack']}")
    print(f"Override: {status['override']}")
