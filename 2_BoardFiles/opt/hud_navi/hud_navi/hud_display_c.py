"""
HUD AR头盔显示 — 纯箭头Turn-by-Turn, 640×400 OLED
内部2x渲染抗锯齿 → smoothscale → drm_show 直显

V3.2: 软件渲染 + drm_show 直显 (绕过 Mali GPU page flip 问题)
"""
import math, logging, threading, os, struct, subprocess, time
from typing import Optional
from config import SCREEN_WIDTH, SCREEN_HEIGHT, SCREEN_FPS, LOW_BATTERY_THRESHOLD
from nav_state import state

logger = logging.getLogger(__name__)

# 内部2x渲染尺寸 (抗锯齿用)
IW, IH = SCREEN_WIDTH * 2, SCREEN_HEIGHT * 2
CX2, CY2 = IW // 2, IH // 2
CN_FONT = "/usr/share/fonts/noto-sans-sc/NotoSansSC-Regular.otf"

# ═══════════════════════════════════════════
# 颜色 — 竞速橙全屏 + 白色文字
# ═══════════════════════════════════════════
C_BG       = (200, 80, 0)       # 全屏橙色底
C_BG_NIGHT = (140, 40, 0)       # 夜间深橙
C_ARROW    = (255, 255, 255)    # 白色箭头
C_DIST     = (255, 255, 255)    # 白色距离
C_ROAD     = (230, 220, 210)    # 浅灰路名
C_BOT      = (220, 210, 200)    # 浅灰底部
C_BAR_BG   = (160, 50, 0, 180)  # 深橙底部栏
C_DIVIDER  = (255, 200, 150, 100) # 浅橙分割线
C_GREEN    = (255, 255, 255)    # 充足白
C_YELLOW   = (255, 240, 100)    # 预警亮黄
C_RED      = (255, 255, 255)    # 告警白
C_BLUE     = (255, 255, 255)    # 在线白
C_GPS_GOOD = (255, 255, 255)
C_GPS_WARN = (255, 240, 100)
C_GPS_BAD  = (255, 100, 80)

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
        self._drm_proc = None  # drm_show 子进程

    # ═══════════════════════════════════════
    def init_pygame(self) -> bool:
        """SDL2 软件渲染 + drm_show 直显, 不需要 GPU"""
        global pygame
        try:
            os.environ['SDL_VIDEODRIVER'] = 'dummy'
            import pygame, pygame.gfxdraw
            pygame.init()
            # 软件渲染离屏面
            self._screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            self._clock = pygame.time.Clock()
            self._internal = pygame.Surface((IW, IH), pygame.SRCALPHA)
            s2 = IW / 640.0
            self._fonts = {
                "dist_big":   self._f(int(86 * s2)),
                "dist_std":   self._f(int(64 * s2)),
                "dist_small": self._f(int(48 * s2)),
                "road":       self._f(int(32 * s2)),
                "bot":        self._f(int(24 * s2)),
                "warn":       self._f(int(22 * s2)),
            }
            # 启动 drm_show (stderr→DEVNULL, 防管道缓冲区泄漏)
            self._drm_proc = subprocess.Popen(
                ['/opt/hud_navi/drm_show'],
                stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True)
            logger.info("HUD: software render + drm_show ready")
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
        if self._drm_proc:
            try: self._drm_proc.stdin.close(); self._drm_proc.wait(timeout=2)
            except: pass
        pygame.quit()

    def toggle_night(self): self._night = not self._night

    # ═══════════════════════════════════════
    def _loop(self):
        while self._running:
            try:
                snap = state.get_snapshot()
                self._anim(snap)
                self._render2x(snap)
                pygame.transform.smoothscale(self._internal, (SCREEN_WIDTH, SCREEN_HEIGHT), self._screen)
                self._emit_frame()
                self._clock.tick(SCREEN_FPS)
                self._frame += 1
            except Exception as e:
                logger.error("HUD loop error: %s", e, exc_info=True)
                time.sleep(0.5)  # 短暂延迟后继续

    def _emit_frame(self):
        raw = pygame.image.tostring(self._screen, "RGBA")
        for attempt in range(3):
            if self._drm_proc is None or self._drm_proc.poll() is not None:
                if self._drm_proc is not None:
                    try: self._drm_proc.kill()
                    except: pass
                    try: self._drm_proc.wait(timeout=0.5)  # 回收僵尸, 防PID泄漏
                    except: pass
                self._drm_proc = subprocess.Popen(
                    ['/opt/hud_navi/drm_show'],
                    stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    start_new_session=True)
            try:
                self._drm_proc.stdin.write(struct.pack('<I', len(raw)))
                self._drm_proc.stdin.write(raw)
                self._drm_proc.stdin.flush()
                self._drm_last_fail = 0  # 成功后重置失败计数
                break
            except (BrokenPipeError, OSError):
                try: self._drm_proc.kill()
                except: pass
                try: self._drm_proc.wait(timeout=0.5)
                except: pass
                self._drm_proc = None
                time.sleep(0.02 * (attempt + 1))  # 退避: 20ms→40ms→60ms
                continue

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
        else:                                 pass  # self._nav_idle() 暂时隐藏
        self._top_dot2x(snap)
        self._dest_name2x(snap)
        msgs = []
        if snap.error_message: msgs.append(snap.error_message)
        if snap.is_off_route:  msgs.append("已偏离路线")
        # AI 摄像头预警 (camera_alert_level: 0=无 1=观察 2=警告 3=危险)
        if snap.camera_alert_level >= 2 and snap.camera_alert_msg:
            if self._frame % 15 < 8:  # 警告/危险级别闪烁
                self._alert_box2x(snap.camera_alert_msg)
        if msgs: self._warn2x(" | ".join(msgs))
        self._bottom2x(snap)

    def _alert_box2x(self, msg):
        """红色方框+白色粗字警示"""
        f = self._fonts["dist_std"]
        s = f.render(msg, True, (255, 255, 255))
        w, h = s.get_width() + 60*2, s.get_height() + 20*2
        x, y = CX2 - w//2, CY2 + 80*2
        pygame.draw.rect(self._internal, (180, 0, 0), (x, y, w, h), border_radius=12)
        pygame.draw.rect(self._internal, (255, 0, 0), (x, y, w, h), 6, border_radius=12)
        self._internal.blit(s, (CX2 - s.get_width()//2, y + 20*2 - s.get_height()//2))

    def _vignette2x(self):
        """极淡四角暗角: 预分配Surface复用, 不每帧创建"""
        if not hasattr(self, '_vig_surf'):
            self._vig_surf = pygame.Surface((IW, IH), pygame.SRCALPHA)
            self._vig_day = None; self._vig_night = None
        a = 50 if self._night else 30
        cache = self._vig_night if self._night else self._vig_day
        if cache is not None and cache[0] == a:
            self._internal.blit(cache[1], (0, 0))
            return
        m = self._vig_surf; m.fill((0, 0, 0, 0))
        m.fill((0, 0, 0, a))
        rx, ry = IW // 2 - 10*2, IH // 2 - 2*2
        pygame.gfxdraw.filled_ellipse(m, CX2, CY2, int(rx), int(ry), (0, 0, 0, 0))
        if self._night: self._vig_night = (a, m.copy())
        else: self._vig_day = (a, m.copy())
        self._internal.blit(m, (0, 0))

    # ═══════════════════════════════════════
    # 箭头 (核心)
    # ═══════════════════════════════════════
    def _nav_active(self, snap):
        d = self._smoothed_dist; t = self._trans_t
        if d < 100:
            scale = 1.15; arrow_a = 255; dk = "dist_big"
        elif d < 500:
            scale = 1.0; arrow_a = 255; dk = "dist_std"
        else:
            scale = 0.85; arrow_a = 240; dk = "dist_small"
        if self._night: arrow_a = 255

        pts = _ARROWS.get(snap.turn_direction, _ARROW_UP)
        sz = int(240 * scale)  # 超大箭头
        arrow_y = CY2 - 160*2  # 箭头贴顶
        # 发光光晕
        if d < 200:
            a0 = int(12 * t)
            if a0 > 0:
                gs = sz + 8
                poly0 = [(CX2 + int(x * gs), arrow_y + int(y * gs)) for x, y in pts]
                pygame.gfxdraw.aapolygon(self._internal, poly0, (255,255,255,a0))
        # 箭头主体
        poly = [(CX2 + int(x * sz), arrow_y + int(y * sz)) for x, y in pts]
        ma = int(arrow_a * t)
        if ma > 0:
            try:
                pygame.gfxdraw.filled_polygon(self._internal, poly, (255,255,255,ma))
                pygame.gfxdraw.aapolygon(self._internal, poly, (255,255,255,ma))
            except Exception:
                pygame.draw.polygon(self._internal, (255,255,255,ma), poly)

        # 距离+方向: "70m 右转" (大号粗字)
        if d > 0:
            ds = f"{int(d)}m" if d < 1000 else f"{d/1000:.1f}km"
            dirs = {"left":"左转","right":"右转","straight":"直行","uturn":"掉头"}
            dr = dirs.get(snap.turn_direction, "")
            self._ctext2x(f"{ds} {dr}", self._fonts["dist_std"], (*C_DIST, int(255 * t)), arrow_y + int(240*2))

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
        # 在线蓝点 (简化: GPS 仅显示在底部栏)
        if snap.online_mode:
            x, y = 14*2, 12*2
            pygame.gfxdraw.filled_circle(self._internal, x, y, 4*2, C_BLUE)
            pygame.gfxdraw.aacircle(self._internal, x, y, 4*2, C_BLUE)
            pygame.gfxdraw.aacircle(self._internal, x, y, 8*2, (*C_BLUE[:3], 50))

    def _dest_name2x(self, snap):
        """右上角目的地名称"""
        name = snap.destination_name
        if not name: return
        s = self._fonts["bot"].render(name, True, C_BOT)
        x = IW - s.get_width() - 12*2
        y = 6*2
        self._internal.blit(s, (x, y))

    # ═══════════════════════════════════════
    def _bottom2x(self, snap):
        h = 56 * 2; y0 = IH - h - 10*2  # 留底部边距, 避开屏线
        # 预分配底部栏 Surface (复用, 不每帧创建)
        if not hasattr(self, '_bottom_bar') or self._bottom_bar.get_height() != h:
            self._bottom_bar = pygame.Surface((IW, h), pygame.SRCALPHA)
        self._bottom_bar.fill(C_BAR_BG); self._internal.blit(self._bottom_bar, (0, y0))

        # 电量颜色
        bat = snap.battery_level
        if bat >= 30:   bat_c = C_GREEN
        elif bat >= 10: bat_c = C_YELLOW
        else:           bat_c = C_RED

        # GPS信号颜色
        gq = snap.gps_quality
        sats = gq.get("satellites", 0) if isinstance(gq, dict) else 0
        if sats >= 8:     gps_c = C_GPS_GOOD
        elif sats >= 4:   gps_c = C_GPS_WARN
        else:             gps_c = C_GPS_BAD

        items = [
            ("Spd", f"{snap.gps_speed:.1f}", "km/h", C_BOT),
            ("Rst", self._fmtr(snap.remaining_distance), "", C_BOT),
            ("ETA", f"{int(snap.eta_minutes)}", "min", C_BOT),
            ("Bat", f"{int(bat)}", "%", bat_c),
            ("GPS", f"{sats}", "", C_BOT),
        ]
        f = self._fonts["bot"]; n = len(items); gap = IW // n
        y = y0 + 4*2
        for i, (label, value, unit, color) in enumerate(items):
            display = f"{label} {value}{unit}"
            s = f.render(display, True, color)
            x = gap * i + (gap - s.get_width()) // 2
            self._internal.blit(s, (x, y))
            if i < n - 1:
                lx = gap * (i + 1)
                pygame.draw.line(self._internal, C_DIVIDER, (lx, y0 + 14*2), (lx, y0 + h - 14*2), 1)

    def _fmtr(self, m):
        return f"{m/1000:.1f} km" if m > 1000 else (f"{int(m)} m" if m > 0 else "--")

    # ═══════════════════════════════════════
    def _warn2x(self, msg):
        s = self._fonts["warn"].render(msg, True, C_RED)
        r = s.get_rect(centerx=CX2, top=28*2)
        pygame.draw.rect(self._internal, (0,0,0,170), r.inflate(14*2, 6*2))
        self._internal.blit(s, r)

    # ═══════════════════════════════════════
    def _ctext2x(self, text, font, color, y):
        s = font.render(text, True, color)
        self._internal.blit(s, (CX2 - s.get_width() // 2, y - s.get_height() // 2))
