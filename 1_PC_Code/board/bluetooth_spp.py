"""
蓝牙通信模块 — Classic Bluetooth SPP (RFCOMM)
跟蓝牙音箱一样配对，通信就是串口读写 JSON

流程:
  1. bt_init → hciconfig hci0 up
  2. sdptool add SP → 注册 "HUD-Navi" 串口服务
  3. rfcomm listen → 等手机配对连接
  4. /dev/rfcomm0 → 读写 JSON (跟 /dev/ttyS8 一样)
"""
import json
import logging
import threading
import subprocess
import os
import time
from typing import Optional, Callable

from config import BLE_DEVICE_NAME
from nav_state import state

logger = logging.getLogger(__name__)

RFCOMM_DEV = "/dev/rfcomm0"
SPP_CHANNEL = 1
SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"  # 标准 SPP UUID


class BluetoothSPP:
    """Classic Bluetooth SPP 通信器 — 跟串口一样简单"""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._rfcomm: Optional[subprocess.Popen] = None
        self._fd = None
        self._connected = False

        # 回调
        self._on_destination: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None

    # ---- 公开接口 (与 BLE 版本完全兼容) ----

    def set_on_destination(self, callback: Callable):
        self._on_destination = callback

    def set_on_cancel(self, callback: Callable):
        self._on_cancel = callback

    def start(self):
        """初始化 SPP 服务 + 开始监听"""
        self._running = True
        self._thread = threading.Thread(
            target=self._spp_loop, daemon=True, name="SPP-Thread"
        )
        self._thread.start()
        logger.info("蓝牙 SPP 线程已启动")

    def stop(self):
        self._running = False
        if self._rfcomm:
            self._rfcomm.terminate()
        if self._fd:
            try:
                self._fd.close()
            except Exception:
                pass
        logger.info("蓝牙 SPP 已停止")

    def send_status(self):
        """向手机发送导航状态"""
        if not self._connected or not self._fd:
            return
        snap = state.get_snapshot()
        msg = {
            "type": "status",
            "lat": snap.current_position[0] if snap.current_position else None,
            "lon": snap.current_position[1] if snap.current_position else None,
            "speed": snap.gps_speed,
            "remaining_distance": round(snap.remaining_distance),
            "eta_minutes": round(snap.eta_minutes),
            "instruction": snap.instruction,
            "instruction_direction": snap.turn_direction,
            "battery": round(snap.battery_level),
            "gps_signal": "weak" if snap.gps_quality["signal_weak"] else "good",
        }
        self._write_json(msg)

    def send_arrived(self, destination_name: str):
        self._write_json({"type": "arrived", "destination": destination_name})

    def send_error(self, message: str):
        self._write_json({"type": "error", "message": message})

    # ---- 内部实现 ----

    def _write_json(self, data: dict):
        """写 JSON 到 RFCOMM 设备 (加换行分隔)"""
        if not self._fd:
            return
        try:
            raw = json.dumps(data, ensure_ascii=False) + "\n"
            self._fd.write(raw.encode())
            self._fd.flush()
        except Exception:
            self._connected = False

    def _spp_loop(self):
        """主循环: 注册 SPP → 监听连接 → 处理消息"""
        try:
            # 1. 注册 SPP 服务 (按官方文档)
            subprocess.run(
                "export $(dbus-launch); sdptool add --channel=1 GATT SP A2SNK A2DP",
                shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
            logger.info("SPP 服务已注册 (GATT+SP)")

            # 2. 注册配对代理 (处理 PIN 码 0000) — D-Bus Agent1 接口
            try:
                import asyncio as _a
                from dbus_next.aio import MessageBus as _MB
                from dbus_next import BusType as _BT, Message as _Msg
                from dbus_next.service import ServiceInterface as _SI, method as _m

                class Agent(_SI):
                    def __init__(self): super().__init__("org.bluez.Agent1")
                    @_m()
                    def Release(self): pass
                    @_m()
                    def RequestPinCode(self, device: "o") -> "s": return "0000"
                    @_m()
                    def DisplayPinCode(self, device: "o", pincode: "s"): pass
                    @_m()
                    def RequestPasskey(self, device: "o") -> "u": return 0
                    @_m()
                    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"): pass
                    @_m()
                    def RequestConfirmation(self, device: "o", passkey: "u"): pass
                    @_m()
                    def RequestAuthorization(self, device: "o"): pass
                    @_m()
                    def AuthorizeService(self, device: "o", uuid: "s"): pass
                    @_m()
                    def Cancel(self): pass

                async def _reg():
                    bus = await _MB(bus_type=_BT.SYSTEM).connect()
                    bus.export("/org/bluez/hud_agent", Agent())
                    await bus.call(_Msg(destination="org.bluez", path="/org/bluez/hci0",
                        interface="org.bluez.AgentManager1", member="RegisterAgent",
                        signature="os", body=["/org/bluez/hud_agent", "KeyboardDisplay"]))
                    await bus.call(_Msg(destination="org.bluez", path="/org/bluez/hci0",
                        interface="org.bluez.AgentManager1", member="RequestDefaultAgent",
                        signature="o", body=["/org/bluez/hud_agent"]))
                _a.run(_reg())
                logger.info("配对代理已注册 (PIN: 0000)")
            except Exception as e:
                logger.warning(f"代理注册失败: {e}")

            # 3. 设置设备名和可见性
            subprocess.run(
                ["hciconfig", "hci0", "name", BLE_DEVICE_NAME],
                timeout=5,
            )
            subprocess.run(
                ["hciconfig", "hci0", "piscan"],
                timeout=5,
            )
            logger.info(f"蓝牙可见: {BLE_DEVICE_NAME}, 等待配对(密码0000)...")

            # 3. 监听 + 处理循环
            while self._running:
                # 启动 rfcomm 监听
                self._rfcomm = subprocess.Popen(
                    ["rfcomm", "listen", "/dev/rfcomm0", str(SPP_CHANNEL)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                # 等手机配对 (最多等 15 秒)
                waited = 0
                while self._running and waited < 15:
                    if os.path.exists(RFCOMM_DEV):
                        break
                    time.sleep(0.5)
                    waited += 0.5

                if not os.path.exists(RFCOMM_DEV):
                    self._rfcomm.terminate()
                    continue

                logger.info("手机已连接!")

                # 4. 打开 RFCOMM 设备 → 当串口用
                try:
                    self._fd = open(RFCOMM_DEV, "r+b", buffering=0)
                    self._connected = True
                    self._read_loop()
                except Exception as e:
                    logger.error(f"RFCOMM 打开失败: {e}")
                finally:
                    self._connected = False
                    if self._fd:
                        try:
                            self._fd.close()
                        except Exception:
                            pass
                        self._fd = None
                    if self._rfcomm:
                        self._rfcomm.terminate()
                    logger.info("手机已断开, 重新监听...")

        except Exception as e:
            logger.exception(f"SPP 主循环异常: {e}")

    def _read_loop(self):
        """从 RFCOMM 读 JSON 命令"""
        buf = b""
        while self._running and self._connected:
            try:
                data = self._fd.read(1)  # 逐字节读
                if not data:
                    break
                if data == b"\n":
                    if buf:
                        self._handle_message(buf)
                        buf = b""
                else:
                    buf += data
            except BlockingIOError:
                pass
            except Exception:
                break

    def _handle_message(self, raw: bytes):
        try:
            msg = json.loads(raw.decode("utf-8"))
            msg_type = msg.get("type", "")
            logger.info(f"SPP收到: {msg_type}")

            if msg_type == "set_destination":
                lat, lon = float(msg["lat"]), float(msg["lon"])
                name = msg.get("name", "目的地")
                logger.info(f"  目的地: {name} ({lat}, {lon})")
                with state as s:
                    s.destination = (lat, lon)
                    s.destination_name = name
                    s.is_navigating = True
                    s.is_arrived = False
                    s.route = []
                if self._on_destination:
                    self._on_destination(lat, lon, name)

            elif msg_type == "cancel_navigation":
                with state as s:
                    s.is_navigating = False
                    s.destination = None
                    s.route = []
                    s.instruction = ""
                if self._on_cancel:
                    self._on_cancel()

            elif msg_type == "query_status":
                self.send_status()

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"SPP消息解析错误: {e}")
