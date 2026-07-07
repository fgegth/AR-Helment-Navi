"""完整HUD效果图：高德地图底图 + AR导航叠加"""
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

# 1. 高德地图底图 (天安门)
p = AmapProvider()
img_data = p.get_map_image((39.9087, 116.3975), W, H)
if img_data:
    map_surf = pygame.image.load(io.BytesIO(img_data))
    screen.blit(map_surf, (0, 0))
else:
    screen.fill((5,5,10))

# 2. 路线
route = [(320,200),(320,160),(340,130),(390,100),(450,90),(500,85),(530,80),(550,75)]
pygame.draw.lines(screen, (0,180,255), False, route, 4)

# 3. 目的地红点
pygame.draw.circle(screen, (255,60,60), (547,72), 8)

# 4. 当前位置 (中心)
cx, cy = W//2, H//2
pygame.draw.circle(screen, (0,220,255), (cx,cy), 8)
pygame.draw.circle(screen, (255,255,255), (cx,cy), 8, 1)
hdg = math.radians(25 + 90)
r = 16
tip = (cx+math.cos(hdg)*r, cy-math.sin(hdg)*r)
lft = (cx+math.cos(hdg+2.6)*r*0.65, cy-math.sin(hdg+2.6)*r*0.65)
rgt = (cx+math.cos(hdg-2.6)*r*0.65, cy-math.sin(hdg-2.6)*r*0.65)
pygame.draw.polygon(screen, (0,220,255), [tip,lft,rgt])

# 5. 方向箭头 (半透明叠加)
arr = fonts["big"].render("→", True, (255,255,255))
ov = pygame.Surface((W,H), pygame.SRCALPHA)
ov.blit(arr, arr.get_rect(centerx=W//2, centery=H//2-60))
screen.blit(ov, (0,0))

# 6. 距离
d = fonts["huge"].render("200 m", True, (255,255,255))
screen.blit(d, d.get_rect(centerx=W//2, centery=H//2+20))

# 7. 路名 (带半透明黑底)
st = fonts["mid"].render("右转进入南池子大街", True, (255,255,255))
sr = st.get_rect(centerx=W//2, centery=H//2+60)
bg = pygame.Surface((sr.w+12, sr.h+6), pygame.SRCALPHA)
bg.fill((0,0,0,140))
screen.blit(bg, (sr.x-6, sr.y-3))
screen.blit(st, sr)

# 8. 顶部状态条
tb = pygame.Surface((W,24), pygame.SRCALPHA)
tb.fill((0,0,0,140))
screen.blit(tb, (0,0))
screen.blit(fonts["tiny"].render("⚡ 87%", True, (0,220,80)), (8,4))
screen.blit(fonts["tiny"].render("🌐 在线", True, (180,180,180)), (W-130,4))
screen.blit(fonts["tiny"].render("📶 GPS:12", True, (0,220,80)), (W-70,4))

# 9. 底部信息栏
bb = pygame.Surface((W,36), pygame.SRCALPHA)
bb.fill((0,0,0,160))
screen.blit(bb, (0, H-36))
y = H-26
screen.blit(fonts["tiny"].render("🚲 18.5 km/h", True, (255,255,255)), (10,y))
screen.blit(fonts["tiny"].render("剩 3.2 km", True, (255,255,255)), (W//3,y))
screen.blit(fonts["tiny"].render("约 12 min", True, (255,255,255)), (W*2//3,y))

pygame.image.save(screen, "/tmp/hud_full.png")
print("DONE")
pygame.quit()
