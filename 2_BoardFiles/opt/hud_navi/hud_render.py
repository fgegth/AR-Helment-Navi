#!/usr/bin/env python3
"""独立 HUD 渲染进程 —— 不和 main.py 共享线程，稳定不崩"""
import os, sys, time, logging, struct, subprocess, threading
sys.path.insert(0, '/opt/hud_navi')
os.environ['SDL_VIDEODRIVER'] = 'dummy'

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(threadName)-10s] %(levelname)s - %(message)s')
logger = logging.getLogger('HUD-Render')

from config import SCREEN_WIDTH, SCREEN_HEIGHT, SCREEN_FPS
from nav_state import state
from hud_display_c import HUDDisplay

def main():
    logger.info("HUD Render Process Starting")
    hud = HUDDisplay()

    if not hud.init_pygame():
        logger.error("HUD init failed, exiting")
        return 1

    hud._running = True

    # 直接主线程跑循环, 不用 daemon thread
    try:
        while True:
            snap = state.get_snapshot()
            hud._anim(snap)
            hud._internal.fill((0,0,0,0))
            hud._render2x(snap)
            import pygame
            pygame.transform.smoothscale(hud._internal, (SCREEN_WIDTH, SCREEN_HEIGHT), hud._screen)
            hud._emit_frame()
            hud._clock.tick(SCREEN_FPS)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("Fatal: %s", e, exc_info=True)
    finally:
        hud.stop()
    return 0

if __name__ == '__main__':
    sys.exit(main())
