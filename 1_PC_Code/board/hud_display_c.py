"""
HUD AR头盔显示 — 纯箭头Turn-by-Turn, 640×400 OLED
内部2x渲染抗锯齿 → smoothscale → 清晰输出
"""
import math, logging, threading, os
from typing import Optional
import pygame, pygame.gfxdraw
from config import SCREEN_WIDTH, SCREEN_HEIGHT, SCREEN_FPS, LOW_BATTERY_THRESHOLD
from nav_state import state

logger = logging.getLogger(__name__)

# 内部2x渲染尺寸 (抗锯齿用)
IW, IH = SCREEN_WIDTH * 2, SCREEN_HEIGHT * 2
CX2, CY2 = IW // 2, IH // 2
CN_FONT = "C:/Windows/Fonts/msyh.ttc"

# ═══════════════════════════════════════════
# 颜色 (OLED防烧屏, 全半透明)
# ═══════════════════════════════════════════
C_BG       = (22, 24, 26)
C_BG_NIGHT = (4, 5, 8)
C_ARROW    = (255, 255, 255)        # 箭头白
C_DIST     = (255, 255, 255)        # 距离白
C_ROAD     = (190, 190, 190)        # 路名
C_BOT      = (130, 130, 130)        # 底部
C_BAR_BG   = (0, 0, 0, 120)
C_GREEN    = (90, 230, 90)
C_YELLOW   = (255, 200, 0)
C_RED      = (255, 55, 55)
C_BLUE     = (85, 165, 255)

# ═══════════════════════════════════════════
# 极简箭头: 粗线宽+圆角收口 (线条造型, 非填充块)
# ═══════════════════════════════════════════
_ARROW_UP    = [(0,-1), (0.15,-0.7), (0.55,-0.15), (0.35,-0.15), (0.35,1), (-0.35,1), (-0.35,-0.15), (-0.55,-0.15), (-0.15,-0.7)]
_ARROW_LEFT  = [(-1,0), (-0.7,-0.15), (-0.15,-0.55), (-0.15,-0.35), (1,-0.35), (1,0.35), (-0.15,0.35), (-0.15,0.55), (-0.7,0.15)]
_ARROW_RIGHT = [(1,0), (0.7,-0.15), (0.15,-0.55), (0.15,-0.35), (-1,-0.35), (-1,0.35), (0.15,0.35), (0.15,0.55), (0.7,0.15)]
_ARROW_UTURN = [(-0.55,-1), (0.55,-1), (0.55,0.3), (0.1,0.3), (0.1,-0.5), (-0.55,-0.5)]
_ARROW_FORK  = [(0,-1), (-0.25,-0.05), (0.6,0.55), (0.3,0.7), (-0.25,0.15)]

_ARROWS = {
    "straight": _ARROW_UP, "left": _ARROW_LEFT, "right": _ARROW_RIGHT,
    "uturn": _ARROW_UTURN, "fork_left": _ARROW_LEFT, "fork_right": _ARROW_FORK,
}


class HUDDisplay:
    def __init__(self):
        self._running = False; self._thread = None; self._screen = None
        self._clock = None; self._frame = 0; self._fonts = {}
        self._prev_dir = ""; self._trans_t = 1.0; self._breath_t = 0.0
        self._smoothed_dist = 0.0; self._night = False
        self._internal = None  # 2x渲染面

    # ═══════════════════════════════════════
    def init_pygame(self) -> bool:
        try:
            os.environ['SDL_VIDEODRIVER'] = 'KMSDRM'
            pygame.init()
            self._screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
            pygame.display.set_caption("HUD AR"); pygame.mouse.set_visible(False)
            self._clock = pygame.time.Clock()
            self._internal = pygame.Surface((IW, IH), pygame.SRCALPHA)
            s2 = IW / 640.0
            self._fonts = {
                "dist_big":   self._f(int(62 * s2)),
                "dist_std":   self._f(int(46 * s2)),
                "dist_small": self._f(int(34 * s2)),
                "road":       self._f(int(22 * s2)),
                "bot":        self._f(int(17 * s2)),
                "warn":       self._f(int(15 * s2)),
            }
            return True
        except Exception as e:
            logger.error(f"HUD: {e}"); return False

    def _f(self, size):
        try: return pygame.font.Font(CN_FONT, size)
        except: return pygame.font.Font(None, size)

    def start(self):
        if not self._screen and not self.init_pygame(): return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="HUD")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread: self._thread.join(timeout=2)
        pygame.quit()

    def toggle_night(self): self._night = not self._night

    # ═══════════════════════════════════════
    def _loop(self):
        while self._running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT: self._running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_ESCAPE: self._running = False
                    elif ev.key == pygame.K_n: self.toggle_night()

            snap = state.get_snapshot()
            self._anim(snap)
            # 渲染到2x内部面
            self._internal.fill((0,0,0,0))
            self._render2x(snap)
            # 缩放抗锯齿到实际画面
            scaled = pygame.transform.smoothscale(self._internal, (SCREEN_WIDTH, SCREEN_HEIGHT))
            self._screen.blit(scaled, (0, 0))
            pygame.display.flip()
            self._clock.tick(SCREEN_FPS)
            self._frame += 1

    def _anim(self, snap):
        cur = snap.turn_direction
        if cur != self._prev_dir:
            self._trans_t = 0.0; self._prev_dir = cur
        if self._trans_t < 1.0: self._trans_t = min(1.0, self._trans_t + 0.1)
        self._breath_t += 0.06
        t = snap.instruction_distance
        self._smoothed_dist += (t - self._smoothed_dist) * 0.3

    # ═══════════════════════════════════════
    # 2x 渲染 (所有绘制在内部1280x800面上)
    # ═══════════════════════════════════════
    def _render2x(self, snap):
        bg = C_BG_NIGHT if self._night else C_BG
        self._internal.fill(bg)

        nav = snap.is_navigating and snap.current_position is not None
        if snap.is_arrived:                   self._nav_arrived()
        elif nav and snap.turn_direction != "arrived": self._nav_active(snap)
        elif not nav and snap.is_navigating:  self._nav_gps_lost()
        else:                                 self._nav_idle()

        self._bottom2x(snap)
        self._top_dot2x(snap)
        if snap.error_message: self._warn2x(snap.error_message)
        self._ellipse2x()

    # ═══════════════════════════════════════
    # 箭头 (核心)
    # ═══════════════════════════════════════
    def _nav_active(self, snap):
        d = self._smoothed_dist; t = self._trans_t
        if d < 100:
            scale = 1.20 + math.sin(self._breath_t) * 0.05
            arrow_a = 240; dk = "dist_big"
        elif d < 500:
            scale = 1.0; arrow_a = 220; dk = "dist_std"
        else:
            scale = 0.82; arrow_a = 170; dk = "dist_small"
        if self._night: arrow_a = min(255, arrow_a + 20)

        pts = _ARROWS.get(snap.turn_direction, _ARROW_UP)
        sz = int(70 * scale)
        # 发光光晕
        for lv in range(4, 0, -1):
            a = int(18 / lv * t)
            if a < 1: continue
            gs = sz + lv * 6
            poly = [(CX2 + int(x * gs), CY2 - 52*2 + int(y * gs)) for x, y in pts]
            for px, py in poly:
                pygame.gfxdraw.filled_circle(self._internal, px, py, lv * 3 + 2, (255,255,255,a))
            pygame.gfxdraw.aapolygon(self._internal, poly, (255,255,255,a))
        # 箭头主体: 粗线条, gfxdraw抗锯齿
        poly = [(CX2 + int(x * sz), CY2 - 52*2 + int(y * sz)) for x, y in pts]
        ma = int(arrow_a * t)
        if ma > 0:
            # gfxdraw 填充+描边
            try:
                pygame.gfxdraw.filled_polygon(self._internal, poly, (255,255,255,ma))
                pygame.gfxdraw.aapolygon(self._internal, poly, (255,255,255,ma))
            except Exception:
                pygame.draw.polygon(self._internal, (255,255,255,ma), poly)

        # 距离数字
        if d > 0:
            ds = f"{d/1000:.1f}km" if d >= 1000 else f"{int(d)}m"
            self._ctext2x(ds, self._fonts[dk], (*C_DIST, int(255 * t)), CY2 + 6*2)

        # 路名 (70%透明)
        road = self._road_name(snap.instruction)
        if road:
            ra = int(180 * t)
            self._ctext2x(road, self._fonts["road"], (*C_ROAD, ra), CY2 + 46*2)

    def _road_name(self, instr):
        if not instr: return ""
        import re
        s = re.sub(r'\d+\.?\d*\s*(米|m|km|公里)', '', instr)
        for w in ["沿","进入","前方"]:
            if w in s:
                rest = s[s.index(w)+len(w):].strip()
                return rest if rest else s
        return s

    # ═══════════════════════════════════════
    def _nav_arrived(self):
        self._ctext2x("✓", self._fonts["dist_big"], C_GREEN, CY2 - 30*2)
        self._ctext2x("已到达目的地", self._fonts["road"], C_GREEN, CY2 + 22*2)

    def _nav_gps_lost(self):
        pts = _ARROW_UP; sz = 55
        poly = [(CX2 + int(x*sz), CY2 - 40*2 + int(y*sz)) for x, y in pts]
        try:
            pygame.gfxdraw.aapolygon(self._internal, poly, (*C_YELLOW, 150))
        except: pass
        self._ctext2x("信号弱 请慢行", self._fonts["road"], (*C_YELLOW, 200), CY2 + 20*2)

    def _nav_idle(self):
        self._ctext2x("等待目的地...", self._fonts["road"], C_BOT, CY2)

    # ═══════════════════════════════════════
    def _top_dot2x(self, snap):
        if snap.online_mode:
            x, y = 14*2, 12*2
            pygame.gfxdraw.filled_circle(self._internal, x, y, 4*2, C_BLUE)
            pygame.gfxdraw.aacircle(self._internal, x, y, 4*2, C_BLUE)
            pygame.gfxdraw.aacircle(self._internal, x, y, 8*2, (*C_BLUE[:3], 50))

    # ═══════════════════════════════════════
    def _bottom2x(self, snap):
        h = 36 * 2; y0 = IH - h
        bar = pygame.Surface((IW, h), pygame.SRCALPHA)
        bar.fill(C_BAR_BG); self._internal.blit(bar, (0, y0))

        items = [
            f"▲ {snap.gps_speed:.1f} km/h",
            f"◆ {self._fmtr(snap.remaining_distance)}",
            f"◉ {int(snap.eta_minutes)} min",
            f"◧ {int(snap.battery_level)}%",
        ]
        f = self._fonts["bot"]; n = len(items); gap = IW // n
        y = y0 + 10*2
        for i, t in enumerate(items):
            s = f.render(t, True, C_BOT)
            x = gap * i + (gap - s.get_width()) // 2
            self._internal.blit(s, (x, y))
            if i < n - 1:
                lx = gap * (i + 1)
                pygame.draw.line(self._internal, (55, 55, 55), (lx, y0 + 8*2), (lx, y0 + h - 8*2), 2)

    def _fmtr(self, m):
        return f"{m/1000:.1f} km" if m > 1000 else (f"{int(m)} m" if m > 0 else "--")

    # ═══════════════════════════════════════
    def _warn2x(self, msg):
        s = self._fonts["warn"].render(msg, True, C_RED)
        r = s.get_rect(centerx=CX2, top=28*2)
        pygame.draw.rect(self._internal, (0,0,0,170), r.inflate(14*2, 6*2))
        self._internal.blit(s, r)

    # ═══════════════════════════════════════
    def _ellipse2x(self):
        """椭圆遮罩+暗角: 椭圆外纯黑, 边缘渐变暗角"""
        m = pygame.Surface((IW, IH), pygame.SRCALPHA)
        # 椭圆外区域纯黑
        m.fill((0, 0, 0, 220))
        # 椭圆内透明
        rx, ry = IW // 2 - 12*2, IH // 2 - 3*2
        pygame.gfxdraw.filled_ellipse(m, CX2, CY2, int(rx), int(ry), (0, 0, 0, 0))
        # 边缘暗角渐变
        for i in range(18):
            t = i / 18.0; a = int(50 * (1 - t) * (1 - t))
            if a < 2: continue
            pygame.gfxdraw.aaellipse(m, CX2, CY2, int(rx - i), int(ry - i), (0, 0, 0, a))
        self._internal.blit(m, (0, 0))

    # ═══════════════════════════════════════
    def _ctext2x(self, text, font, color, y):
        s = font.render(text, True, color)
        self._internal.blit(s, (CX2 - s.get_width() // 2, y - s.get_height() // 2))
