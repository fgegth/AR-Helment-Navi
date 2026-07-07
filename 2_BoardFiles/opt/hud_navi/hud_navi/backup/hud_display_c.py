"""
HUD显示模块 — 640×400 AR 头戴导航界面
负责人: C

设计原则 (适配 SONY ECX336C 0.23" Micro OLED + 光学放大):
  - 地图底图提供空间方向感 (高德静态地图)
  - AR 叠加层提供导航指令 (大箭头 + 大字距离)
  - 所有文字 ≥ 16px, 关键数字 ≥ 36px
  - 配色高对比度, 半透明叠加不遮挡地图

图层顺序 (底→顶):
  1. 地图底图 (高德静态地图 640×400, 中心=GPS当前位置)
  2. 规划路线 (蓝色折线, 半透明)
  3. 当前位置标记 (中心蓝点 + 方向三角)
  4. 方向指示 (大号箭头图标, 半透明白)
  5. 距离+路名文字
  6. 顶部状态条 (电量 / GPS信号 / 在线状态)
  7. 底部信息栏 (速度 / 剩余距离 / ETA)
"""
import io
import math
import logging
import threading
from typing import Optional

import pygame

from config import (
    SCREEN_WIDTH, SCREEN_HEIGHT, SCREEN_FPS,
    AMAP_MAP_ZOOM, LOW_BATTERY_THRESHOLD, WEAK_GPS_SIGNAL_THRESHOLD,
)
from nav_state import state

logger = logging.getLogger(__name__)

# ============================================================
# 颜色常量 — 高对比度, 适配 OLED
# ============================================================
C_BG       = (0, 0, 0)           # OLED 纯黑 = 像素关闭
C_ROUTE    = (0, 180, 255, 180)  # 路线蓝, 半透明
C_POS      = (0, 220, 255)       # 当前位置青蓝
C_DEST     = (255, 60, 60)       # 目的地红
C_ARROW    = (255, 255, 255, 200)# 方向箭头白, 半透明
C_TEXT     = (255, 255, 255)     # 主文字白
C_TEXT_DIM = (160, 160, 160)     # 次要文字灰
C_BAR_BG   = (0, 0, 0, 150)     # 状态栏背景, 半透明黑
C_WARN     = (255, 200, 0)       # 警告黄
C_DANGER   = (255, 50, 50)       # 危险红
C_GREEN    = (0, 220, 80)        # 正常绿


class HUDDisplay:
    """HUD 渲染器 — 运行在独立线程 30fps"""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._screen: Optional[pygame.Surface] = None
        self._clock: Optional[pygame.time.Clock] = None

        # 地图缓存
        self._map_surf: Optional[pygame.Surface] = None
        self._map_center: Optional[tuple] = None
        self._map_request_frame: int = -1000

        # 字体 (延迟初始化)
        self._f = {}

        # 外部依赖注入
        self._map_provider = None  # NavigationProvider

    # ---- 公开接口 (main.py 调用) ----

    def set_map_provider(self, provider):
        self._map_provider = provider

    def init_pygame(self) -> bool:
        try:
            pygame.init()
            # 调试阶段用窗口模式, 成品改 FULLSCREEN
            self._screen = pygame.display.set_mode(
                (SCREEN_WIDTH, SCREEN_HEIGHT),
                pygame.DOUBLEBUF,
            )
            pygame.display.set_caption("HUD AR Navi")
            pygame.mouse.set_visible(False)
            self._clock = pygame.time.Clock()

            # 字体 — 用系统默认, 不同尺寸
            self._f = {
                "huge":   pygame.font.Font(None, 56),  # 距离大字
                "big":    pygame.font.Font(None, 38),  # 方向箭头
                "mid":    pygame.font.Font(None, 24),  # 路名
                "small":  pygame.font.Font(None, 18),  # 状态条
                "tiny":   pygame.font.Font(None, 14),  # 底部栏
            }
            logger.info(f"HUD 初始化: {SCREEN_WIDTH}×{SCREEN_HEIGHT}")
            return True
        except pygame.error as e:
            logger.error(f"HUD 初始化失败: {e}")
            return False

    def start(self):
        if self._screen is None:
            if not self.init_pygame():
                return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="HUD")
        self._thread.start()
        logger.info("HUD 线程已启动")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        pygame.quit()

    # ================================================================
    # 渲染主循环 (30fps)
    # ================================================================

    def _loop(self):
        frame = 0
        while self._running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self._running = False
                elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    self._running = False

            snap = state.get_snapshot()
            self._render(snap, frame)
            self._clock.tick(SCREEN_FPS)
            frame += 1

    def _render(self, snap, frame: int):
        """逐层渲染"""
        self._screen.fill(C_BG)

        # L1: 地图底图
        self._layer_map(snap, frame)

        # L2: 路线
        if snap.route and snap.current_position:
            self._layer_route(snap)

        # L3: 目的地标记
        if snap.destination and snap.current_position:
            self._layer_destination(snap)

        # L4: 当前位置
        if snap.current_position:
            self._layer_position(snap)

        # L5: 方向箭头 (大号)
        if snap.is_navigating and snap.turn_direction != "arrived":
            self._layer_direction(snap)

        # L6: 距离 + 路名文字
        if snap.is_navigating and snap.instruction:
            self._layer_instruction(snap)

        # L7: 到达提示
        if snap.is_arrived:
            self._layer_arrived()

        # L8: 顶部状态条
        self._layer_status_bar(snap)

        # L9: 底部信息栏
        if snap.is_navigating:
            self._layer_info_bar(snap)

        # L10: 覆盖警告
        if snap.error_message:
            self._layer_warning(snap.error_message)

        pygame.display.flip()

    # ================================================================
    # L1: 地图底图
    # ================================================================

    def _layer_map(self, snap, frame: int):
        """每隔 3 秒或移动超过 80 米更新地图"""
        if self._map_provider is None or snap.current_position is None:
            return

        center = snap.current_position

        # 判断是否需要更新
        need = self._map_surf is None
        if self._map_center and not need:
            from gps_reader_a import haversine_distance
            d = haversine_distance(
                self._map_center[0], self._map_center[1],
                center[0], center[1],
            )
            # 每 3 秒或移动 > 80 米
            if d > 80 or (frame - self._map_request_frame > SCREEN_FPS * 3):
                need = True

        if need:
            self._map_request_frame = frame
            img_data = self._map_provider.get_map_image(
                center, SCREEN_WIDTH, SCREEN_HEIGHT
            )
            if img_data:
                try:
                    self._map_surf = pygame.image.load(io.BytesIO(img_data))
                    self._map_center = center
                except pygame.error:
                    pass

        if self._map_surf:
            self._screen.blit(self._map_surf, (0, 0))

    # ================================================================
    # L2: 路线折线
    # ================================================================

    def _layer_route(self, snap):
        if len(snap.route) < 2:
            return
        pts = []
        for lat, lon in snap.route:
            x, y = self._geo2px(lat, lon, snap.current_position)
            pts.append((x, y))

        if len(pts) >= 2:
            # 半透明蓝色粗线 (需创建临时 surface)
            overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            pygame.draw.lines(overlay, C_ROUTE, False, pts, width=4)
            self._screen.blit(overlay, (0, 0))

    # ================================================================
    # L3: 目的地标记
    # ================================================================

    def _layer_destination(self, snap):
        lat, lon = snap.destination
        x, y = self._geo2px(lat, lon, snap.current_position)
        # 红色脉冲圈
        pulse = 8 + abs(((pygame.time.get_ticks() % 1200) / 600.0) - 1) * 5
        pygame.draw.circle(self._screen, C_DEST, (int(x), int(y)), int(pulse), 2)
        pygame.draw.circle(self._screen, C_DEST, (int(x), int(y)), 4)

    # ================================================================
    # L4: 当前位置 (屏幕中心)
    # ================================================================

    def _layer_position(self, snap):
        cx, cy = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2

        # 蓝色圆点
        pygame.draw.circle(self._screen, C_POS, (cx, cy), 6)
        pygame.draw.circle(self._screen, (255, 255, 255), (cx, cy), 6, 1)

        # 方向三角
        hdg = math.radians(-snap.gps_heading + 90)
        r = 16
        tip = (cx + math.cos(hdg) * r, cy - math.sin(hdg) * r)
        lft = (cx + math.cos(hdg + 2.6) * (r * 0.65),
               cy - math.sin(hdg + 2.6) * (r * 0.65))
        rgt = (cx + math.cos(hdg - 2.6) * (r * 0.65),
               cy - math.sin(hdg - 2.6) * (r * 0.65))
        pygame.draw.polygon(self._screen, C_POS, [tip, lft, rgt])

    # ================================================================
    # L5: 方向指示箭头
    # ================================================================

    def _layer_direction(self, snap):
        arrows = {"left": "←", "right": "→", "uturn": "↩", "straight": "↑"}
        glyph = arrows.get(snap.turn_direction, "↑")

        # 大号箭头 (半透明, 居中偏上)
        overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        txt = self._f["big"].render(glyph, True, C_ARROW[:3])
        rect = txt.get_rect(centerx=SCREEN_WIDTH // 2, centery=SCREEN_HEIGHT // 2 - 60)
        overlay.blit(txt, rect)
        self._screen.blit(overlay, (0, 0))

    # ================================================================
    # L6: 距离 & 路名
    # ================================================================

    def _layer_instruction(self, snap):
        cx = SCREEN_WIDTH // 2

        # 距离 (大字)
        if snap.instruction_distance > 0:
            if snap.instruction_distance >= 1000:
                dist_str = f"{snap.instruction_distance / 1000:.1f} km"
            else:
                dist_str = f"{int(snap.instruction_distance)} m"
        else:
            dist_str = ""

        if dist_str:
            txt = self._f["huge"].render(dist_str, True, C_TEXT)
            rect = txt.get_rect(centerx=cx, centery=SCREEN_HEIGHT // 2 + 20)
            self._screen.blit(txt, rect)

        # 路名/指令
        instr = snap.instruction.replace(dist_str, "").strip().lstrip("前方")
        if instr:
            txt = self._f["mid"].render(instr, True, C_TEXT)
            rect = txt.get_rect(centerx=cx, centery=SCREEN_HEIGHT // 2 + 60)
            self._screen.blit(txt, rect)

    # ================================================================
    # L7: 到达提示
    # ================================================================

    def _layer_arrived(self):
        cx, cy = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2
        txt = self._f["big"].render("已到达", True, C_GREEN)
        rect = txt.get_rect(centerx=cx, centery=cy)
        self._screen.blit(txt, rect)

    # ================================================================
    # L8: 顶部状态条
    # ================================================================

    def _layer_status_bar(self, snap):
        bar_h = 22
        bar = pygame.Surface((SCREEN_WIDTH, bar_h), pygame.SRCALPHA)
        bar.fill(C_BAR_BG)
        self._screen.blit(bar, (0, 0))

        # 左侧: 电量
        batt_c = C_DANGER if snap.battery_level < LOW_BATTERY_THRESHOLD else C_GREEN
        self._blit_text(f"⚡{int(snap.battery_level)}%", 8, 3, "tiny", batt_c)

        # 右侧: GPS 信号
        gps = snap.gps_quality
        gps_c = C_WARN if gps["signal_weak"] else C_GREEN
        gps_str = f"📶{gps['satellites']}"
        self._blit_text(gps_str, SCREEN_WIDTH - 70, 3, "tiny", gps_c)

        # 右侧: 在线状态
        mode_str = "在线" if snap.online_mode else "离线"
        self._blit_text(mode_str, SCREEN_WIDTH - 130, 3, "tiny", C_TEXT_DIM)

    # ================================================================
    # L9: 底部信息栏
    # ================================================================

    def _layer_info_bar(self, snap):
        bar_h = 34
        bar = pygame.Surface((SCREEN_WIDTH, bar_h), pygame.SRCALPHA)
        bar.fill(C_BAR_BG)
        self._screen.blit(bar, (0, SCREEN_HEIGHT - bar_h))

        y = SCREEN_HEIGHT - bar_h + 8

        # 速度
        spd = f"🚲{snap.gps_speed:.1f} km/h"
        self._blit_text(spd, 10, y, "tiny", C_TEXT)

        # 剩余距离
        rm = snap.remaining_distance
        if rm > 1000:
            rm_s = f"剩{rm/1000:.1f}km"
        elif rm > 0:
            rm_s = f"剩{int(rm)}m"
        else:
            rm_s = "--"
        self._blit_text(rm_s, SCREEN_WIDTH // 3, y, "tiny", C_TEXT)

        # ETA
        eta = f"约{int(snap.eta_minutes)}min"
        self._blit_text(eta, SCREEN_WIDTH * 2 // 3, y, "tiny", C_TEXT)

    # ================================================================
    # L10: 警告横幅
    # ================================================================

    def _layer_warning(self, msg: str):
        txt = self._f["small"].render(msg, True, C_DANGER)
        rect = txt.get_rect(centerx=SCREEN_WIDTH // 2, top=28)
        bg = rect.inflate(16, 6)
        pygame.draw.rect(self._screen, (0, 0, 0), bg)
        self._screen.blit(txt, rect)

    # ================================================================
    # 工具方法
    # ================================================================

    def _geo2px(self, lat: float, lon: float, center: tuple) -> tuple:
        """经纬度 → 屏幕像素 (中心=center点)"""
        if center is None:
            return (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
        lat0, lon0 = center
        dy = (lat - lat0) * 111320.0
        dx = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
        # zoom≈15 时约 1.2 像素/米
        scale = 1.2
        px = SCREEN_WIDTH / 2 + dx * scale
        py = SCREEN_HEIGHT / 2 - dy * scale
        return (px, py)

    def _blit_text(self, text: str, x: int, y: int,
                    font_key: str, color: tuple):
        """便捷文字渲染"""
        surf = self._f[font_key].render(text, True, color)
        self._screen.blit(surf, (x, y))
