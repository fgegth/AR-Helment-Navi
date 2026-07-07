"""HUD效果图 — 高德地图 + 真实骑行路线"""
import os, math, io
os.environ["SDL_VIDEODRIVER"] = "dummy"
import pygame
from map_api_c import AmapProvider

pygame.init()
W, H = 640, 400
screen = pygame.display.set_mode((W, H))
fonts = {
    "huge": pygame.font.Font(None, 56), "big": pygame.font.Font(None, 38),
    "mid": pygame.font.Font(None, 24),  "small": pygame.font.Font(None, 18),
    "tiny": pygame.font.Font(None, 14),
}

p = AmapProvider()
center = (39.9087, 116.3975)  # 天安门附近

# 1. 高德静态地图
img = p.get_map_image(center, W, H)
if img:
    screen.blit(pygame.image.load(io.BytesIO(img)), (0,0))
else:
    screen.fill((5,5,10))
    print("Map failed")

# 2. 真实骑行路线 — 高德API
dest = (39.903182, 116.397755)  # 天安门广场
route = p.get_route(center, dest)
if route and len(route) > 1:
    # 经纬度→屏幕像素 (以center为中心)
    def geo2px(lat, lon):
        dy = (lat - center[0]) * 111320
        dx = (lon - center[1]) * 111320 * math.cos(math.radians(center[0]))
        scale = 2.0
        return (W/2 + dx*scale, H/2 - dy*scale)

    pts = [geo2px(lat, lon) for lat, lon in route]
    pygame.draw.lines(screen, (0, 180, 255), False, pts, 4)
    print(f"Route: {len(route)} points")

    # 终点标记
    end_x, end_y = pts[-1]
    pygame.draw.circle(screen, (255,60,60), (int(end_x), int(end_y)), 8)

# 3. 当前位置
cx, cy = W//2, H//2
pygame.draw.circle(screen, (0,220,255), (cx,cy), 8)
pygame.draw.circle(screen, (255,255,255), (cx,cy), 8, 1)
hdg = math.radians(220 + 90)  # 朝向西南
r = 16
tip = (cx+math.cos(hdg)*r, cy-math.sin(hdg)*r)
lft = (cx+math.cos(hdg+2.6)*r*0.65, cy-math.sin(hdg+2.6)*r*0.65)
rgt = (cx+math.cos(hdg-2.6)*r*0.65, cy-math.sin(hdg-2.6)*r*0.65)
pygame.draw.polygon(screen, (0,220,255), [tip,lft,rgt])

# 4. 方向+距离
arr = fonts["big"].render("↙", True, (255,255,255))
ov = pygame.Surface((W,H), pygame.SRCALPHA)
ov.blit(arr, arr.get_rect(centerx=W//2, centery=H//2-50))
screen.blit(ov, (0,0))
d = fonts["huge"].render("500 m", True, (255,255,255))
screen.blit(d, d.get_rect(centerx=W//2, centery=H//2+20))

# 5. 路名
st = fonts["mid"].render("前方进入南池子大街", True, (255,255,255))
sr = st.get_rect(centerx=W//2, centery=H//2+55)
bg = pygame.Surface((sr.w+12, sr.h+6), pygame.SRCALPHA)
bg.fill((0,0,0,140)); screen.blit(bg, (sr.x-6, sr.y-3)); screen.blit(st, sr)

# 6. 状态条
tb = pygame.Surface((W,24), pygame.SRCALPHA); tb.fill((0,0,0,140)); screen.blit(tb, (0,0))
screen.blit(fonts["tiny"].render("⚡ 87%", True, (0,220,80)), (8,4))
screen.blit(fonts["tiny"].render("🌐 在线", True, (180,180,180)), (W-130,4))
screen.blit(fonts["tiny"].render("📶 GPS:12", True, (0,220,80)), (W-70,4))

# 7. 底部
bb = pygame.Surface((W,36), pygame.SRCALPHA); bb.fill((0,0,0,160)); screen.blit(bb, (0,H-36))
y = H-26
screen.blit(fonts["tiny"].render("🚲 18.5 km/h", True, (255,255,255)), (10,y))
screen.blit(fonts["tiny"].render("剩 1.2 km", True, (255,255,255)), (W//3,y))
screen.blit(fonts["tiny"].render("约 5 min", True, (255,255,255)), (W*2//3,y))

pygame.image.save(screen, "/tmp/hud_real.png")
print("DONE")
pygame.quit()
