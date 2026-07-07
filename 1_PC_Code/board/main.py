"""
HUD 骑行导航系统 — 主程序入口
架构: 多线程 + 共享状态

线程分工:
  GPS-Thread     (1Hz)  : GPS串口读取 → 写入state.current_position
  BLE-Thread     (事件) : 蓝牙接收目的地 → 写入state.destination
  Nav-Thread     (1Hz)  : 路径规划 / 偏航检测 / 导航指令 / 语音播报
  HUD-Thread     (30fps): 读取state快照 → 渲染画面

启动顺序:
  1. 初始化硬件 (串口/蓝牙/屏幕)
  2. 加载地图Provider (在线优先, 离线降级)
  3. 启动各工作线程
  4. 主线程监控系统状态
"""
import logging
import signal
import sys
import threading
import time

from config import (
    ROUTE_MODE, OSM_MAP_FILE, OFFLINE_TILE_DIR,
    LOW_BATTERY_THRESHOLD, GPS_COLD_START_SEC,
    BATTERY_FULL_V, BATTERY_CUTOFF_V,
    BATTERY_VOLTAGE_DIVIDER, BATTERY_ADC_CHANNEL,
    WEAK_GPS_SIGNAL_THRESHOLD, NETWORK_CHECK_INTERVAL,
)
from nav_state import state
from gps_reader_a import GPSReader
# from bluetooth_link_c import BluetoothLink  # 暂不需要蓝牙
from route_planner_b import RoutePlanner
from map_api_c import AmapProvider, OfflineProvider, check_network
from hud_display_c import HUDDisplay
from voice_alert_c import VoiceAlert
from http_server import start_server
from road_safety import analyze_route
from smart_features import record_destination, ride_log_point, ride_end, check_speed, get_frequent_places

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)-10s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ============================================================
# 导航引擎线程 (核心业务逻辑)
# ============================================================
class NavigationEngine:
    """
    导航决策引擎
    在GPS线程中驱动（每次GPS更新触发一次导航逻辑）
    """

    def __init__(self):
        self._planner = RoutePlanner()
        self._running = False

        # Provider管理
        self._online_provider = None
        self._offline_provider = None
        self._active_provider = None

        # 网络恢复检测
        self._last_network_check = 0.0
        self._was_offline = False

    def init_providers(self):
        """初始化地图Provider（在线优先 + 离线降级）"""
        logger.info("初始化地图服务...")

        # 在线
        self._online_provider = AmapProvider()
        online_ok = self._online_provider.is_available()
        logger.info(f"  在线模式(高德API): {'可用' if online_ok else '不可用'}")

        # 离线
        self._offline_provider = OfflineProvider(OSM_MAP_FILE, OFFLINE_TILE_DIR)
        offline_ok = self._offline_provider.is_available()
        logger.info(f"  离线模式(OSM):    {'可用' if offline_ok else '不可用'}")

        # 选择Provider
        if ROUTE_MODE == "online" and online_ok:
            self._active_provider = self._online_provider
            with state as s:
                s.online_mode = True
        elif ROUTE_MODE == "offline" and offline_ok:
            self._active_provider = self._offline_provider
            with state as s:
                s.online_mode = False
        elif ROUTE_MODE == "auto":
            if online_ok:
                self._active_provider = self._online_provider
                with state as s:
                    s.online_mode = True
            elif offline_ok:
                self._active_provider = self._offline_provider
                with state as s:
                    s.online_mode = False
            else:
                logger.warning("无可用地图服务！SPP/HUD照常运行")
                self._active_provider = None
                with state as s:
                    s.online_mode = False
        else:
            logger.warning("无可用地图服务！导航将不可用，但SPP/HUD正常")
            self._active_provider = None
            with state as s:
                s.online_mode = False

        if self._active_provider:
            logger.info(f"  当前模式: {'在线' if state.get_snapshot().online_mode else '离线'}")
        return True

    def start(self):
        self._running = True
        logger.info("导航引擎就绪")

    def stop(self):
        self._running = False

    def tick(self, voice_alert: VoiceAlert):
        """
        每次GPS更新后调用（约1Hz）
        执行完整的导航决策链
        """
        if not self._running:
            return

        # 定期检测网络恢复 (离线→在线自动切回)
        self._check_and_recover_network()

        snap = state.get_snapshot()

        # 无GPS定位，跳过
        if snap.current_position is None:
            with state as s:
                if "GPS定位中" not in s.error_message and "GPS信号弱" not in s.error_message:
                    s.error_message = "等待GPS定位..."
            return

        current_pos = snap.current_position

        # ---- 到达检测 ----
        if snap.is_navigating and snap.destination:
            if self._planner.is_arrived(current_pos, snap.destination):
                with state as s:
                    s.is_arrived = True
                    s.is_navigating = False
                    s.instruction = "已到达目的地"
                    s.turn_direction = "arrived"
                voice_alert.speak(
                    f"您已到达{snap.destination_name}", force=True
                )
                # 保存骑行记录
                try:
                    ride_end(snap.destination_name, snap.remaining_distance / 1000.0)
                except Exception:
                    pass
                logger.info(f"已到达目的地: {snap.destination_name}")
                return

        # ---- 路径规划 ----
        if snap.is_navigating and snap.destination:
            need_plan = False

            # 检查是否需要规划路线
            if not snap.route:
                need_plan = True
            elif self._planner.is_off_route(current_pos, snap.route):
                need_plan = True
                with state as s:
                    s.is_off_route = True
                logger.info("检测到偏航，重新规划路线...")

            if need_plan:
                self._plan_route(current_pos, snap.destination)

        # ---- 获取导航指令 ----
        if snap.route and snap.is_navigating:
            result = self._planner.get_next_instruction(
                current_pos, snap.route, snap.route_steps, snap.gps_heading,
            )

            # 剩余距离和ETA
            remaining, eta = self._planner.get_remaining_info(
                current_pos, snap.route,
            )

            with state as s:
                s.instruction = result["instruction"]
                s.instruction_distance = result["distance"]
                s.turn_direction = result["direction"]
                s.remaining_distance = remaining
                s.eta_minutes = eta
                s.is_off_route = False
                s.error_message = ""

            # 语音播报
            if result["should_voice"]:
                voice_alert.speak(result["instruction"])
            else:
                # 如果已经过了路口，重置播报标志
                if result["distance"] > 0 and result["direction"] == "straight":
                    self._planner.reset_voice_alert()

        # 清除GPS等待消息 (有定位后自动清除)
        with state as s:
            if "GPS定位中" in s.error_message or s.error_message == "等待GPS定位...":
                s.error_message = ""

    def _check_and_recover_network(self):
        """
        定期检查网络状态, 实现离线→在线自动恢复
        当检测到网络恢复时, 自动切换回在线Provider
        """
        now = time.time()
        if now - self._last_network_check < NETWORK_CHECK_INTERVAL:
            return
        self._last_network_check = now

        online = check_network()

        if online and not state.get_snapshot().online_mode:
            # 网络已恢复, 切回在线
            if self._online_provider and self._online_provider.is_available():
                self._active_provider = self._online_provider
                with state as s:
                    s.online_mode = True
                logger.info("🌐 网络已恢复, 自动切换为在线模式")
                self._was_offline = True

        elif not online and state.get_snapshot().online_mode:
            # 网络断开, 标记 (实际降级在 _plan_route 中触发)
            self._was_offline = True

    def _plan_route(self, origin, destination):
        """执行路径规划 + 在线→离线降级 + 离线→在线恢复"""
        logger.info(f"规划路线: {origin} → {destination}")

        # 兜底: 如果在线Provider被标记不可用, 检查是否已恢复
        if self._active_provider is None:
            with state as s:
                s.error_message = "无可用地图服务"
            return

        # 冷却: 上次规划失败后等 30 秒再重试
        if not hasattr(self, '_last_plan_fail'):
            self._last_plan_fail = 0
        if self._last_plan_fail and time.time() - self._last_plan_fail < 30:
            return

        # 在线模式下先检查网络, 不通则预降级
        if self._active_provider is self._online_provider and not check_network():
            if self._offline_provider and self._offline_provider.is_available():
                logger.warning("网络不通, 预降级到离线模式")
                self._active_provider = self._offline_provider
                with state as s:
                    s.online_mode = False

        result = None
        # 优先在线调用 get_route_with_steps
        if self._active_provider is self._online_provider:
            result = self._active_provider.get_route_with_steps(origin, destination)
            # 在线规划失败 → 降级到离线
            if result is None and self._offline_provider and self._offline_provider.is_available():
                logger.warning("在线规划失败，降级到离线模式")
                self._active_provider = self._offline_provider
                with state as s:
                    s.online_mode = False
                result = self._active_provider.get_route_with_steps(origin, destination)
        elif self._active_provider is self._offline_provider:
            result = self._active_provider.get_route_with_steps(origin, destination)

        if result:
            mode = "online" if self._active_provider is self._online_provider else "offline"
            route_coords = result["coords"]
            route_steps = result["steps"]
            with state as s:
                s.route = route_coords
                s.route_steps = route_steps
                s.route_mode = mode
            self._planner.reset_off_route_counter()
            self._planner.reset_voice_alert()
            # 道路安全分析 (使用实际路线步骤)
            safety = analyze_route(route_steps)
            with state as s:
                emoji = safety.get("current_emoji", safety.get("current_level", "🟡"))
                road = safety.get("current_road", "")
                s.road_condition = emoji + road
                s.road_summary = safety.get("summary", "")
                s.road_upcoming = safety.get("upcoming", [])
                if s.destination:
                    record_destination(s.destination_name, s.destination[0], s.destination[1])
            logger.info(f"路线规划成功: {len(route_coords)}个航点, {len(route_steps)}个步骤 ({mode}模式)")
        else:
            with state as s:
                s.error_message = "路径规划失败"
            self._last_plan_fail = time.time()
            logger.error("路径规划失败，30秒后重试")


# ============================================================
# 电池监测 (ADC)
# ============================================================

def read_battery_adc() -> int:
    """
    读取 QSM368ZP ADC 原始值
    返回: 0~1023 (10位ADC), 失败返回 -1
    """
    adc_path = f"/sys/bus/iio/devices/iio:device0/{BATTERY_ADC_CHANNEL}"
    try:
        with open(adc_path, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return -1


def adc_to_battery_percent(adc_raw: int) -> float:
    """
    ADC原始值 → 电池百分比

    计算链:
      ADC_raw → 电压(ADC端) = raw × 1.8V / 1024
      电池端电压 = ADC端电压 × 分压比
      百分比 = (V_now - V_cutoff) / (V_full - V_cutoff) × 100
    """
    if adc_raw < 0:
        return 0.0

    adc_v = adc_raw * 1.8 / 1024.0           # ADC 引脚电压
    battery_v = adc_v * BATTERY_VOLTAGE_DIVIDER  # 实际电池电压

    if battery_v >= BATTERY_FULL_V:
        return 100.0
    if battery_v <= BATTERY_CUTOFF_V:
        return 0.0

    percent = (battery_v - BATTERY_CUTOFF_V) / (BATTERY_FULL_V - BATTERY_CUTOFF_V) * 100
    return round(percent, 1)


# ============================================================
# 系统监测
# ============================================================

def check_system_status():
    """
    系统状态监测 (由主线程周期性调用)
    检查电池电量、GPS信号等
    """
    snap = state.get_snapshot()

    # ---- 电池监测 ----
    adc_raw = read_battery_adc()
    if adc_raw >= 0:
        battery_pct = adc_to_battery_percent(adc_raw)
        with state as s:
            s.battery_level = battery_pct
    # ADC 读取失败时保持上一次电量值不变

    # ---- GPS弱信号警告 ----
    if snap.gps_quality["signal_weak"] and snap.is_navigating:
        with state as s:
            if "GPS信号弱" not in s.error_message:
                s.error_message = "GPS信号弱"

    # ---- 低电量警告 ----
    if snap.battery_level < LOW_BATTERY_THRESHOLD:
        with state as s:
            if "电量低" not in s.error_message:
                # 不覆盖GPS信号弱警告, 追加
                existing = s.error_message
                if existing and "电量低" not in existing:
                    s.error_message = existing + " | 电量低"
                elif not existing:
                    s.error_message = "电量低"


# ============================================================
# 优雅退出
# ============================================================
_shutdown_flag = False


def signal_handler(sig, frame):
    global _shutdown_flag
    logger.info("收到退出信号，正在关闭...")
    _shutdown_flag = True


# ============================================================
# 主入口
# ============================================================
def main():
    global _shutdown_flag

    logger.info("=" * 60)
    logger.info("  HUD 骑行导航系统 v0.1")
    logger.info("=" * 60)

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # ---- 1. 初始化各模块 ----
    logger.info("正在初始化各模块...")

    gps = GPSReader()
    # ble = BluetoothLink()  # 暂不需要蓝牙
    ble = None
    nav_engine = NavigationEngine()
    hud = HUDDisplay()
    voice = VoiceAlert()

    # ---- 2. 初始化地图 ----
    if not nav_engine.init_providers():
        logger.error("无法初始化地图服务，退出")
        return 1

    # ---- 3. 初始化硬件 ----
    gps.open()
    # HUD暂时跳过，等SDL2编译完再开
    # if not hud.init_pygame():
    #     logger.warning("HUD初始化失败，HTTP/GPS/导航继续运行")
    # 不退出！没有显示器照样跑HTTP+GPS+导航

    # ---- 4. 注册回调 (BLE暂不需要) ----
    if ble:
        ble.set_on_destination(
            lambda lat, lon, name: logger.info(f"新目的地: {name}")
        )
        ble.set_on_cancel(
            lambda: logger.info("导航已取消")
        )

    # ---- 4.5. 语音命令回调 ----
    def handle_voice_command(cmd_text):
        """语音识别回调: 自然语言 → 导航动作"""
        import time as _time
        try:
            from intent_engine import extract_intent
        except ImportError:
            logger.warning("intent_engine 不可用")
            return
        result = extract_intent(cmd_text)
        i = result["intent"]
        logger.info("Whisper原始: %s", cmd_text)
        logger.info(f"语音意图: {i}, 目标: {result.get('target','')}")
        with state as s:
            s.voice_last_cmd = i
            s.voice_last_raw = cmd_text
            s.voice_last_time = _time.time()
        if i == "navigate" and result.get("lat") and result.get("lon"):
            with state as s:
                s.destination = (result["lat"], result["lon"])
                s.destination_name = result["target"]
                s.is_navigating = True
                s.is_arrived = False
                s.route = []
            logger.info(f"语音导航: {result['target']}")
        elif i == "cancel":
            with state as s:
                s.is_navigating = False; s.destination = None; s.route = []
            logger.info("语音取消导航")
        elif i == "status":
            snap = state.get_snapshot()
            msg = f"剩余{snap.remaining_distance/1000:.1f}公里, 约{int(snap.eta_minutes)}分钟" if snap.is_navigating else "当前没有导航"
            voice.speak(msg, force=True)
        elif i == "continue":
            snap2 = state.get_snapshot()
            if snap2.destination:
                with state as s: s.is_navigating = True
                logger.info("语音继续导航")
            else:
                voice.speak("没有已保存的目的地", force=True)

    # ---- 5. 启动各线程 ----
    logger.info("启动工作线程...")
    logger.info("DEBUG: 1-GPS"); gps.start()
    if ble: ble.start()  # BLE暂不需要
    logger.info("DEBUG: 3-NAV"); nav_engine.start()
    logger.info("DEBUG: 4-VOICE"); voice.start()
    try:
        from voice_command import listen_and_execute
        def _voice_loop():
            while True:
                try: listen_and_execute(handle_voice_command)
                except Exception as e: logger.warning(f"VoiceCmd: {e}")
        threading.Thread(target=_voice_loop, daemon=True, name="VoiceCmd").start()
        logger.info("语音命令线程已启动")
    except Exception as e:
        logger.warning(f"语音命令不可用: {e}")
    # hud.start()  # HUD暂时跳过
    threading.Thread(target=start_server, daemon=True, name="HTTP").start()

    # ---- 5.5. GPS 冷启动等待 ----
    logger.info(f"等待GPS定位... (ATGM336H-5N 冷启动约{GPS_COLD_START_SEC}秒)")
    gps_fix_acquired = False
    wait_start = time.time()
    while not _shutdown_flag and (time.time() - wait_start) < GPS_COLD_START_SEC:
        snap = state.get_snapshot()
        if snap.current_position is not None and not snap.gps_quality["signal_weak"]:
            gps_fix_acquired = True
            logger.info(f"GPS定位成功! ({snap.current_position})")
            break

        # 更新HUD状态提示
        elapsed = int(time.time() - wait_start)
        with state as s:
            s.error_message = f"GPS定位中... {elapsed}s"
        time.sleep(1.0)

    if not gps_fix_acquired:
        logger.warning("GPS冷启动超时，继续等待定位...")
        with state as s:
            s.error_message = "GPS信号弱，持续搜索中..."
    else:
        with state as s:
            s.error_message = ""

    logger.info("系统启动完成，开始导航主循环")

    # ---- 6. 主循环：GPS驱动导航逻辑 ----
    try:
        tick_count = 0
        while not _shutdown_flag:
            time.sleep(1.0)  # 1Hz GPS更新频率

            # 驱动导航引擎 (内含在线↔离线切换)
            nav_engine.tick(voice)

            # 行车记录 + 超速检测
            snap = state.get_snapshot()
            if snap.current_position and snap.is_navigating:
                pos = snap.current_position
                ride_log_point(pos[0], pos[1], snap.gps_speed)
                # 超速
                result = check_speed(snap.gps_speed, snap.road_condition or "caution")
                if result["over"]:
                    with state as s:
                        if "超速" not in (s.error_message or ""):
                            s.error_message = result["warning"]

            # 系统监测（每10秒）
            tick_count += 1
            if tick_count % 10 == 0:
                check_system_status()

            # 定期向手机发送状态（每5秒）
            if tick_count % 5 == 0:
                snap = state.get_snapshot()
                if snap.is_navigating and ble:
                    ble.send_status()

    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        logger.exception(f"主循环异常: {e}")
    finally:
        # ---- 7. 优雅关闭 ----
        logger.info("正在关闭系统...")
        try: hud.stop()
        except Exception: pass
        voice.stop()
        nav_engine.stop()
        if ble: ble.stop()
        gps.stop()
        logger.info("系统已关闭")

    return 0


if __name__ == "__main__":
    sys.exit(main())
