"""
蓝牙通信模块 — BLE Peripheral (GATT Server)
负责人: C

技术: dbus-fast (纯 Python D-Bus) + BlueZ GATT API
  无需 dbus-python / python3-gi / GLib, 全部 pip install 搞定

协议(JSON):
  手机 → HUD (Write): {"type":"set_destination","lat":39.9,"lon":116.4,"name":"xxx"}
  手机 → HUD (Write): {"type":"cancel_navigation"}
  手机 → HUD (Write): {"type":"query_status"}
  HUD → 手机 (Notify): {"type":"status","speed":...,"instruction":...,...}
  HUD → 手机 (Notify): {"type":"arrived","destination":"xxx"}
  HUD → 手机 (Notify): {"type":"error","message":"xxx"}
"""
import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from typing import Optional, Callable

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Message, MessageType
from dbus_fast.service import ServiceInterface, method, signal, PropertyAccess
from dbus_fast.constants import PropertyAccess as PA

from config import (
    BLE_SERVICE_UUID, BLE_CHAR_UUID_RX, BLE_CHAR_UUID_TX, BLE_DEVICE_NAME,
)
from nav_state import state

logger = logging.getLogger(__name__)

# BlueZ 常量
BLUEZ = "org.bluez"
ADAPTER_PATH = "/org/bluez/hci0"
GATT_MGR_IFACE = "org.bluez.GattManager1"
ADV_MGR_IFACE = "org.bluez.LEAdvertisingManager1"
GATT_SVC_IFACE = "org.bluez.GattService1"
GATT_CHR_IFACE = "org.bluez.GattCharacteristic1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
APP_PATH = "/org/bluez/hud_navi/app"
SVC_PATH = APP_PATH + "/service0"
RX_CHR_PATH = SVC_PATH + "/char0"
TX_CHR_PATH = SVC_PATH + "/char1"


class BluetoothLink:
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_destination: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None
        self._pending_notify: list = []
        self._pending_lock = threading.Lock()
        self._tx_notifying = False
        self._bus = None

    # ---- 公开接口 ----
    def set_on_destination(self, callback: Callable):
        self._on_destination = callback

    def set_on_cancel(self, callback: Callable):
        self._on_cancel = callback

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="BLE"
        )
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)

    def send_status(self):
        snap = state.get_snapshot()
        self._enqueue_notify({
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
        })

    def send_arrived(self, name: str):
        self._enqueue_notify({"type": "arrived", "destination": name})

    def send_error(self, msg: str):
        self._enqueue_notify({"type": "error", "message": msg})

    # ---- 内部 ----
    def _enqueue_notify(self, data: dict):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        with self._pending_lock:
            self._pending_notify.append(raw)

    def _handle_rx(self, data: bytes):
        try:
            msg = json.loads(data.decode("utf-8"))
            t = msg.get("type", "")
            logger.info(f"BLE收到: {t}")
            if t == "set_destination":
                lat, lon = float(msg["lat"]), float(msg["lon"])
                name = msg.get("name", "目的地")
                with state as s:
                    s.destination = (lat, lon)
                    s.destination_name = name
                    s.is_navigating = True
                    s.is_arrived = False
                    s.route = []
                if self._on_destination:
                    self._on_destination(lat, lon, name)
            elif t == "cancel_navigation":
                with state as s:
                    s.is_navigating = False
                    s.destination = None
                    s.route = []
                    s.instruction = ""
                if self._on_cancel:
                    self._on_cancel()
            elif t == "query_status":
                self.send_status()
        except Exception as e:
            logger.error(f"BLE解析错误: {e}")

    # ---- asyncio 主循环 ----
    def _run_loop(self):
        asyncio.run(self._ble_main())

    async def _ble_main(self):
        self._init_hw()
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # 注册 GATT 应用
        await self._register_gatt()

        # 启动广告
        await self._start_advertise()

        logger.info(f"BLE就绪: {BLE_DEVICE_NAME}, 等待手机连接...")

        # 定期发送通知
        while self._running:
            await asyncio.sleep(0.2)
            with self._pending_lock:
                if self._pending_notify:
                    raw = self._pending_notify.pop(0)
                    await self._send_notify(raw)

        await self._stop_advertise()

    def _init_hw(self):
        bt = "/usr/bin/bt_init.sh"
        if os.path.exists(bt):
            try:
                subprocess.run(["sh", bt], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)
                time.sleep(1)
            except Exception:
                pass
        # 确保 bluetoothd 运行
        try:
            r = subprocess.run(["pgrep", "bluetoothd"], stdout=subprocess.PIPE, timeout=3)
            if r.returncode != 0:
                subprocess.Popen(["/usr/libexec/bluetooth/bluetoothd", "-E", "-C", "-n"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
        except Exception:
            pass

    # ---- GATT 注册 ----
    async def _register_gatt(self):
        # 导出 D-Bus 对象树 (Application / Service / 2 Characteristics)
        self._bus.export(APP_PATH, AppInterface())
        self._bus.export(SVC_PATH, GattSvcInterface(BLE_SERVICE_UUID, [RX_CHR_PATH, TX_CHR_PATH]))
        self._bus.export(RX_CHR_PATH, CharInterface(BLE_CHAR_UUID_RX, ["write"], self._handle_rx))
        self._bus.export(TX_CHR_PATH, CharInterface(BLE_CHAR_UUID_TX, ["notify"], None, self))

        # 注册到 BlueZ GATT Manager
        await self._bus.call(
            Message(destination=BLUEZ, path=ADAPTER_PATH,
                    interface=GATT_MGR_IFACE, member="RegisterApplication",
                    signature="oa{sv}", body=[APP_PATH, {}])
        )
        logger.info("GATT Application 注册成功")

    # ---- 广告 ----
    async def _start_advertise(self):
        adv_data = {
            "LocalName": ("s", BLE_DEVICE_NAME),
            "ServiceUUIDs": ("as", [BLE_SERVICE_UUID]),
        }
        adv_props = {"Type": ("s", "peripheral")}
        try:
            await self._bus.call(
                Message(destination=BLUEZ, path=ADAPTER_PATH,
                        interface=ADV_MGR_IFACE, member="RegisterAdvertisement",
                        signature="oa{sv}a{sv}",
                        body=[APP_PATH + "/ad", adv_data, adv_props])
            )
            logger.info("BLE广告已启动")
        except Exception:
            # 降级: hciconfig
            try:
                subprocess.run(["hciconfig", "hci0", "leadv", "3"], timeout=5)
            except Exception:
                pass

    async def _stop_advertise(self):
        try:
            await self._bus.call(
                Message(destination=BLUEZ, path=ADAPTER_PATH,
                        interface=ADV_MGR_IFACE, member="UnregisterAdvertisement",
                        signature="o", body=[APP_PATH + "/ad"])
            )
        except Exception:
            pass

    async def _send_notify(self, data: bytes):
        # 通过 PropertiesChanged 通知已订阅的客户端
        if not self._tx_notifying:
            return
        try:
            await self._bus.call(
                Message(destination=None, path=TX_CHR_PATH,
                        interface=PROPS_IFACE, member="PropertiesChanged",
                        signature="sa{sv}as",
                        body=[GATT_CHR_IFACE, {"Value": ("ay", list(data))}, []])
            )
        except Exception:
            pass

    def set_notifying(self, val: bool):
        self._tx_notifying = val


# ============================================================
# D-Bus 接口类 (dbus-fast ServiceInterface)
# ============================================================

class AppInterface(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.DBus.ObjectManager")

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        return {
            SVC_PATH: {
                GATT_SVC_IFACE: {
                    "UUID": ("s", BLE_SERVICE_UUID),
                    "Primary": ("b", True),
                    "Characteristics": ("ao", [RX_CHR_PATH, TX_CHR_PATH]),
                }
            },
            RX_CHR_PATH: {
                GATT_CHR_IFACE: {
                    "UUID": ("s", BLE_CHAR_UUID_RX),
                    "Service": ("o", SVC_PATH),
                    "Flags": ("as", ["write"]),
                }
            },
            TX_CHR_PATH: {
                GATT_CHR_IFACE: {
                    "UUID": ("s", BLE_CHAR_UUID_TX),
                    "Service": ("o", SVC_PATH),
                    "Flags": ("as", ["notify"]),
                }
            },
        }


class GattSvcInterface(ServiceInterface):
    def __init__(self, uuid, chars):
        super().__init__(GATT_SVC_IFACE)
        self._uuid = uuid
        self._chars = chars

    @method()
    def GetProperties(self) -> "a{sv}":
        return {
            "UUID": ("s", self._uuid),
            "Primary": ("b", True),
            "Characteristics": ("ao", self._chars),
        }


class CharInterface(ServiceInterface):
    def __init__(self, uuid, flags, write_cb, owner=None):
        super().__init__(GATT_CHR_IFACE)
        self._uuid = uuid
        self._flags = flags
        self._write_cb = write_cb
        self._value = bytearray()
        self._notifying = False
        self._owner = owner

    @method()
    def GetProperties(self) -> "a{sv}":
        return {
            "UUID": ("s", self._uuid),
            "Service": ("o", SVC_PATH),
            "Flags": ("as", self._flags),
        }

    @method()
    def WriteValue(self, value: "ay"):
        self._value = bytearray(value)
        if self._write_cb:
            self._write_cb(bytes(value))

    @method()
    def ReadValue(self) -> "ay":
        return list(self._value)

    @method()
    def StartNotify(self):
        self._notifying = True
        if self._owner:
            self._owner.set_notifying(True)

    @method()
    def StopNotify(self):
        self._notifying = False
        if self._owner:
            self._owner.set_notifying(False)
