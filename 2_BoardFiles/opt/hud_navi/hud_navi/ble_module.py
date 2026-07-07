"""BLE GATT Server — 完整版: 分包拼接 + 导航触发 + 状态回传"""
import asyncio, json
from dbus_next.aio import MessageBus
from dbus_next import BusType, Message, Variant
from dbus_next.service import ServiceInterface, method, dbus_property, PropertyAccess

BLUEZ = "org.bluez"
ADAPTER = "/org/bluez/hci0"
APP = "/org/bluez/hud_navi"; SVC = APP+"/service0"; RX=SVC+"/char0"; TX=SVC+"/char1"
SVC_IF="org.bluez.GattService1"; CHR_IF="org.bluez.GattCharacteristic1"
OM_IF="org.freedesktop.DBus.ObjectManager"; ADV_IF="org.bluez.LEAdvertisement1"

S_UUID="0000fff0-0000-1000-8000-00805f9b34fb"
R_UUID="0000fff1-0000-1000-8000-00805f9b34fb"
T_UUID="0000fff2-0000-1000-8000-00805f9b34fb"

rx_buf = b""          # 分包缓冲区
nav_state = None      # 外部注入

def set_nav_state(s):
    global nav_state
    nav_state = s

def try_parse():
    """拼好 JSON → 写入导航状态 → 返回解析结果"""
    global rx_buf
    try:
        msg = json.loads(rx_buf.decode("utf-8"))
        rx_buf = b""
        t = msg.get("type","")
        print(f"BLE收到完整: {t}")
        if t == "set_destination" and nav_state:
            lat, lon = float(msg["lat"]), float(msg["lon"])
            name = msg.get("name","目的地")
            with nav_state as s:
                s.destination = (lat, lon)
                s.destination_name = name
                s.is_navigating = True
                s.is_arrived = False
                s.route = []
            print(f"  目的地: {name} ({lat}, {lon})")
        elif t == "cancel_navigation" and nav_state:
            with nav_state as s:
                s.is_navigating = False; s.destination = None; s.route = []
                s.instruction = ""
            print("  导航已取消")
        elif t == "query_status":
            pass  # 下次轮询时会自动回传
        return msg
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    except Exception as e:
        rx_buf = b""
        print(f"解析错误: {e}")
        return None

class AppObj(ServiceInterface):
    def __init__(s): super().__init__(OM_IF)
    @method()
    def GetManagedObjects(s) -> "a{oa{sa{sv}}}":
        return {SVC:{SVC_IF:{"UUID":Variant("s",S_UUID),"Primary":Variant("b",True),
            "Characteristics":Variant("ao",[RX,TX])}},
            RX:{CHR_IF:{"UUID":Variant("s",R_UUID),"Service":Variant("o",SVC),
            "Flags":Variant("as",["write"])}},
            TX:{CHR_IF:{"UUID":Variant("s",T_UUID),"Service":Variant("o",SVC),
            "Flags":Variant("as",["notify"])}}}

class SvcObj(ServiceInterface):
    def __init__(s): super().__init__(SVC_IF)
    @dbus_property(PropertyAccess.READ)
    def UUID(s) -> "s": return S_UUID
    @dbus_property(PropertyAccess.READ)
    def Primary(s) -> "b": return True
    @dbus_property(PropertyAccess.READ)
    def Characteristics(s) -> "ao": return [RX, TX]

class RxObj(ServiceInterface):
    def __init__(s): super().__init__(CHR_IF)
    @method()
    def ReadValue(s) -> "ay": return []
    @method()
    def WriteValue(s, value: "ay", options: "a{sv}"):
        global rx_buf
        rx_buf += bytes(value)
        try_parse()
    @dbus_property(PropertyAccess.READ)
    def UUID(s) -> "s": return R_UUID
    @dbus_property(PropertyAccess.READ)
    def Service(s) -> "o": return SVC
    @dbus_property(PropertyAccess.READ)
    def Flags(s) -> "as": return ["write"]

class TxObj(ServiceInterface):
    def __init__(s): super().__init__(CHR_IF)
    @method()
    def ReadValue(s) -> "ay": return []
    @method()
    def StartNotify(s): pass
    @method()
    def StopNotify(s): pass
    @dbus_property(PropertyAccess.READ)
    def UUID(s) -> "s": return T_UUID
    @dbus_property(PropertyAccess.READ)
    def Service(s) -> "o": return SVC
    @dbus_property(PropertyAccess.READ)
    def Flags(s) -> "as": return ["notify"]

class AdvObj(ServiceInterface):
    def __init__(s): super().__init__(ADV_IF)
    @method()
    def Release(s): pass
    @dbus_property(PropertyAccess.READ)
    def Type(s) -> "s": return "peripheral"
    @dbus_property(PropertyAccess.READ)
    def LocalName(s) -> "s": return "HUD-Navi"
    @dbus_property(PropertyAccess.READ)
    def ServiceUUIDs(s) -> "as": return [S_UUID]
    @dbus_property(PropertyAccess.READ)
    def IncludeTxPower(s) -> "b": return True

async def run_ble():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    bus.export(APP, AppObj()); bus.export(SVC, SvcObj())
    bus.export(RX, RxObj()); bus.export(TX, TxObj())
    bus.export(APP+"/adv", AdvObj())

    await bus.call(Message(destination=BLUEZ,path=ADAPTER,
        interface="org.bluez.GattManager1",member="RegisterApplication",
        signature="oa{sv}",body=[APP,{}]))
    await bus.call(Message(destination=BLUEZ,path=ADAPTER,
        interface="org.bluez.LEAdvertisingManager1",member="RegisterAdvertisement",
        signature="oa{sv}",body=[APP+"/adv",{}]))
    print("BLE GATT + 广告 已注册")

    # 定期推送导航状态
    while True:
        await asyncio.sleep(2)
        if not nav_state:
            continue
        snap = nav_state.get_snapshot()
        if snap.is_navigating:
            payload = json.dumps({
                "type":"status","speed":snap.gps_speed,
                "remaining_distance":round(snap.remaining_distance),
                "eta_minutes":round(snap.eta_minutes),
                "instruction":snap.instruction,
                "instruction_direction":snap.turn_direction,
                "battery":round(snap.battery_level),
                "gps_signal":"weak" if snap.gps_quality["signal_weak"] else "good",
            }).encode()
            # 分片通知 (MTU 兼容)
            for i in range(0, len(payload), 20):
                chunk = list(payload[i:i+20])
                await bus.call(Message(
                    destination=None, path=TX,
                    interface="org.freedesktop.DBus.Properties",
                    member="PropertiesChanged",
                    signature="sa{sv}as",
                    body=[CHR_IF, {"Value": Variant("ay", bytes(chunk))}, []]))
                await asyncio.sleep(0.05)

if __name__ == "__main__":
    from nav_state import state
    set_nav_state(state)
    asyncio.run(run_ble())
