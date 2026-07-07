# HUD 显示问题 — SDL2/pygame 版本冲突

## 问题现象

HDMI 屏幕不显示 HUD 导航画面（箭头、距离等），pygame 无法初始化显示。

## 根因

板子上存在两个互不兼容的 SDL2 版本：

| 来源 | SDL2 版本 | 显示后端 | 问题 |
|------|----------|---------|------|
| 系统 /usr/lib/ (SDK编译) | 2.0.7 | Wayland + KMSDRM | ✅ 正确 |
| miniforge 的 pygame 自带 | 2.28.4 | 只有 offscreen/dummy | ❌ 无显示驱动 |

pygame 加载 miniforge 的 SDL2 2.28.4（无显示后端），无法初始化屏幕。
系统 SDL2 2.0.7 有 Wayland+KMSDRM，但 pygame 版本太新，拒绝加载旧版 SDL2：

```
RuntimeError: Dynamic linking causes SDL downgrade!
(compiled with version 2.28.4, linked to 2.0.7)
```

## 解决方案

用 Buildroot SDK 将 Python3 + SDL2 + pygame 全部从源码编译进固件，保证版本匹配。

### Buildroot menuconfig 勾选清单

```
Target packages →
  Interpreter languages & scripting →
    [*] python3

  Graphic libraries and applications →
    [*] sdl2
        [*] KMS/DRM video driver
        [*] Wayland video driver
        [*] OpenGL ES

  External python modules →
    [*] python-pygame
```

### 关系

Python3(逻辑控制) → SDL2(底层显示+Wayland/KMSDRM) → pygame(Python UI工具)

### 验证

编译后确认产物存在：
```
find output/target -name "python3" -type f
find output/target -name "pygame" -type d
find output/target -name "libSDL2*"
```

刷入固件后确认系统 Python 路径：
```
which python3        # 应该是 /usr/bin/python3，不是 /opt/miniforge3/
python3 -c "import pygame; print(pygame.version.ver)"
```

## 已知尝试（已废弃）

- 外部 pysdl2_dll wheel 替换 SDL2 → KMSDRM 只能输出到 LVDS，不到 HDMI
- pygame 降级安装 → 所有 pip wheel 都绑定 SDL2 2.28.x，无法匹配系统 2.0.7
- 手动拷贝 Wayland .so → 依赖链缺失，SDL2 动态加载失败
- 绕开 pygame 版本检查 → pygame 内部 API 不兼容，崩溃
