"""
BLE 通信模块 — dbus-next 验证版本 (整合进 main.py)
"""
import asyncio, json, logging, subprocess, threading, time
from typing import Optional, Callable

from dbus_next.aio import MessageBus
from dbus_next import BusType, Message, Variant
from dbus_next.service import ServiceInterface, method, dbus_property, PropertyAccess

from config import BLE_SERVICE_UUID, BLE_CHAR_UUID_RX, BLE_CHAR_UUID_TX, BLE_DEVICE_NAME
from nav_state import state

logger = logging.getLogger(__name__)

BLUEZ="org.bluez"; ADAPTER="/org/bluez/hci0"
APP="/org/bluez/hud_navi"; SVC=APP+"/service0"; RX=SVC+"/char0"; TX=SVC+"/char1"
SVC_IF="org.bluez.GattService1"; CHR_IF="org.bluez.GattCharacteristic1"
OM_IF="org.freedesktop.DBus.ObjectManager"; ADV_IF="org.bluez.LEAdvertisement1"

rx_buf = b""
pending = []
_lock = threading.Lock()

class BluetoothLink:
    def __init__(self):
        self._running = False; self._thread = None
        self._on_dest: Optional[Callable] = None
        self._on_cancel: Optional[Callable] = None

    def set_on_destination(self, cb): self._on_dest = cb
    def set_on_cancel(self, cb): self._on_cancel = cb

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._runner, daemon=True, name="BLE")
        self._thread.start()
        logger.info("BLE线程已启动")

    def stop(self):
        self._running = False
        if self._thread: self._thread.join(timeout=3)

    def send_status(self):
        snap = state.get_snapshot()
        data = json.dumps({
            "type":"status","speed":snap.gps_speed,
            "remaining_distance":round(snap.remaining_distance),
            "eta_minutes":round(snap.eta_minutes),"instruction":snap.instruction,
            "instruction_direction":snap.turn_direction,
            "battery":round(snap.battery_level),
            "gps_signal":"weak" if snap.gps_quality["signal_weak"] else "good"
        }).encode()
        with _lock: pending.append(data)

    def send_arrived(self, name): pass
    def send_error(self, msg): pass

    def _runner(self): asyncio.run(self._main())

    async def _main(self):
        subprocess.run("sdptool add --channel=1 GATT SP A2SNK A2DP 2>/dev/null", shell=True)
        time.sleep(0.5)

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        class App(ServiceInterface):
            def __init__(s): super().__init__(OM_IF)
            @method()
            def GetManagedObjects(s) -> "a{oa{sa{sv}}}":
                return {SVC:{SVC_IF:{"UUID":Variant("s",BLE_SERVICE_UUID),"Primary":Variant("b",True),
                    "Characteristics":Variant("ao",[RX,TX])}},
                    RX:{CHR_IF:{"UUID":Variant("s",BLE_CHAR_UUID_RX),"Service":Variant("o",SVC),
                    "Flags":Variant("as",["write"])}},
                    TX:{CHR_IF:{"UUID":Variant("s",BLE_CHAR_UUID_TX),"Service":Variant("o",SVC),
                    "Flags":Variant("as",["notify"])}}}

        class SvcObj(ServiceInterface):
            def __init__(s): super().__init__(SVC_IF)
            @dbus_property(PropertyAccess.READ)
            def UUID(s)->"s": return BLE_SERVICE_UUID
            @dbus_property(PropertyAccess.READ)
            def Primary(s)->"b": return True
            @dbus_property(PropertyAccess.READ)
            def Characteristics(s)->"ao": return [RX,TX]

        class RxChar(ServiceInterface):
            def __init__(s): super().__init__(CHR_IF)
            @method()
            def ReadValue(s)->"ay": return []
            @method()
            def WriteValue(s, value:"ay", options:"a{sv}"):
                global rx_buf
                rx_buf += bytes(value)
                try:
                    msg = json.loads(rx_buf.decode("utf-8"))
                    rx_buf = b""; t = msg.get("type","")
                    logger.info(f"BLE收到: {t}")
                    if t == "set_destination":
                        lat, lon = float(msg["lat"]), float(msg["lon"])
                        name = msg.get("name","目的地")
                        with state as s:
                            s.destination=(lat,lon); s.destination_name=name
                            s.is_navigating=True; s.is_arrived=False; s.route=[]
                        logger.info(f"  目的地: {name}")
                        if self._on_dest: self._on_dest(lat,lon,name)
                    elif t == "cancel_navigation":
                        with state as s: s.is_navigating=False; s.destination=None; s.route=[]
                        if self._on_cancel: self._on_cancel()
                    elif t == "query_status": self.send_status()
                except (json.JSONDecodeError,UnicodeDecodeError): pass
                except Exception as e: rx_buf=b""; logger.error(f"BLE解析: {e}")
            @dbus_property(PropertyAccess.READ)
            def UUID(s)->"s": return BLE_CHAR_UUID_RX
            @dbus_property(PropertyAccess.READ)
            def Service(s)->"o": return SVC
            @dbus_property(PropertyAccess.READ)
            def Flags(s)->"as": return ["write"]

        class TxChar(ServiceInterface):
            def __init__(s): super().__init__(CHR_IF)
            @method()
            def ReadValue(s)->"ay": return []
            @method()
            def StartNotify(s): pass
            @method()
            def StopNotify(s): pass
            @dbus_property(PropertyAccess.READ)
            def UUID(s)->"s": return BLE_CHAR_UUID_TX
            @dbus_property(PropertyAccess.READ)
            def Service(s)->"o": return SVC
            @dbus_property(PropertyAccess.READ)
            def Flags(s)->"as": return ["notify"]

        class AdvObj(ServiceInterface):
            def __init__(s): super().__init__(ADV_IF)
            @method()
            def Release(s): pass
            @dbus_property(PropertyAccess.READ)
            def Type(s)->"s": return "peripheral"
            @dbus_property(PropertyAccess.READ)
            def LocalName(s)->"s": return BLE_DEVICE_NAME
            @dbus_property(PropertyAccess.READ)
            def ServiceUUIDs(s)->"as": return [BLE_SERVICE_UUID]
            @dbus_property(PropertyAccess.READ)
            def IncludeTxPower(s)->"b": return True

        bus.export(APP, App()); bus.export(SVC, SvcObj())
        bus.export(RX, RxChar()); bus.export(TX, TxChar())
        bus.export(APP+"/adv", AdvObj())

        await bus.call(Message(destination=BLUEZ,path=ADAPTER,
            interface="org.bluez.GattManager1",member="RegisterApplication",
            signature="oa{sv}",body=[APP,{}]))
        await bus.call(Message(destination=BLUEZ,path=ADAPTER,
            interface="org.bluez.LEAdvertisingManager1",member="RegisterAdvertisement",
            signature="oa{sv}",body=[APP+"/adv",{}]))
        logger.info("BLE GATT+广告 注册成功")

        # HCI 广告补刀 — 每 1.5 秒重发，手机看到就能连
        import os as _os
        _ad_count = 0
        while self._running:
            await asyncio.sleep(1.5)
            # stop → set data → enable
            _os.system("hcitool cmd 0x08 0x000A 00 >/dev/null 2>&1")
            _os.system("hcitool cmd 0x08 0x0008 1F 02 01 06 11 07 FB 34 9B 5F 80 00 00 80 00 10 00 00 F0 FF 00 00 0A 09 48 55 44 2D 4E 61 76 69 >/dev/null 2>&1")
            _os.system("hcitool cmd 0x08 0x000A 01 >/dev/null 2>&1")
            _ad_count += 1
            if _ad_count % 10 == 0:
                logger.debug(f"HCI广告心跳 #{_ad_count}")
            with _lock:
                if pending:
                    data = pending.pop(0)
                    for i in range(0, len(data), 20):
                        try:
                            await bus.call(Message(
                                destination=None, path=TX,
                                interface="org.freedesktop.DBus.Properties",
                                member="PropertiesChanged",
                                signature="sa{sv}as",
                                body=[CHR_IF,{"Value":Variant("ay",bytes(data[i:i+20]))},[]]))
                            await asyncio.sleep(0.05)
                        except Exception: pass
