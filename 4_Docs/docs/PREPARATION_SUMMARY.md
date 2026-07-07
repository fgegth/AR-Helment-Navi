# 准备阶段总结 — 技术选型与接口确认

> 基于移远 QSM368ZP-WF SDK 完整分析 | 2026-06-28

---

## 一、SDK 资源清单 (已提取)

### 1.1 技术文档

| 文档 | 路径 | 核心内容 |
|------|------|----------|
| **蓝牙用户指导 v1.1** | `Software/Bluetooth/` | BlueZ bt_init.sh + bluetoothctl 操作 |
| **外设驱动开发指导 v1.2** | `Software/Peripheral/` | ADC/SPI/I2C/UART/USB/GPIO/GMAC 全部驱动 |
| **Display 开发说明 v1.0** | `Software/Display/` | MIPI/eDP/HDMI 三屏异显配置 |
| **Audio 开发说明** | `Software/Audio/` | ALSA 声卡配置 |
| **用户指导 v1.0** | `Hardware/` | 硬件接口定义、引脚图、连接器规格 |
| **原理图 V2.2** | `Hardware/` | 完整电路原理图 |

### 1.2 SDK 示例代码

| 示例 | 路径 | 用途 |
|------|------|------|
| **UART 收发** | `examples/uart/UartRTtest.c` | GPS 串口通信参考 |
| **GPIO 控制** | `examples/gpio/gpio0_d3_control.c` | sysfs GPIO 操作（电量检测参考） |
| **RKNN 车牌识别** | `examples/rknn/rknn_LPRNet_demo/` | NPU AI 推理（可选增强） |
| **RKNN 语音识别** | `examples/rknn/rknn_whisper_demo/` | 离线语音指令（可选增强） |
| **RKNN 目标检测** | `examples/rknn/rknn_yolov5_demo/` | 行人/车辆检测（可选增强） |

---

## 二、关键硬件能力确认

### 2.1 平台规格

```
SoC:      RK3568 (四核 Cortex-A55 @ 2.0GHz)
GPU:      Mali G52 (支持 OpenGL ES, 硬件加速 pygame 渲染)
NPU:      1 TOPS (可选 AI 增强)
RAM:      2GB LPDDR4X
存储:     32GB eMMC
OS:       Linux (Kernel 4.19)
```

### 2.2 与项目相关的接口

| 接口 | 数量 | 项目用途 |
|------|------|----------|
| **HDMI 2.0** | 1 路 | ✅ HUD 屏幕输出 (支持 1024×600 ~ 4K) |
| **UART** | 4 路 | ✅ 外接 GPS 模组 (UART8 /dev/ttyS8) |
| **蓝牙 4.2** | 板载 Realtek | ✅ 手机通信 (BLE Peripheral) |
| **ADC** | 5 通道 | ✅ 电池电压监测 (/sys/bus/iio/) |
| **GPIO** | 多个 | ✅ 可扩展 |
| **音频输出** | 扬声器/听筒 | ✅ TTS 语音播报 |
| **USB 3.0/2.0** | 多路 | 备用 (USB GPS 可选) |
| **Wi-Fi 5** | 2.4/5GHz | 在线地图更新 |

---

## 三、BLE 蓝牙方案决策 ⭐

### 3.1 SDK 提供的蓝牙能力

- **芯片**: Realtek 蓝牙 4.2 (BR/EDR + BLE)
- **驱动**: `hci_uart.ko` → `rtk_hciattach` → `/dev/ttyS1` @ 115200
- **协议栈**: BlueZ 5.x (标准 Linux 蓝牙协议栈)
- **初始化**: `bt_init.sh` (一键启动蓝牙)
- **SDK 文档**: 覆盖了 Classic Bluetooth (配对/扫描/OBEX文件传输)，BLE GATT 未详细说明

### 3.2 技术选型对比

| 方案 | 稳定性 | 开发难度 | 依赖 | 推荐 |
|------|--------|----------|------|------|
| **bleak** (Python async BLE) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ 简单 | `pip install bleak` | ✅ **首选** |
| **pydbus + BlueZ D-Bus** | ⭐⭐⭐⭐ | ⭐⭐ 较复杂 | `apt install pydbus` | 🟡 备选 |
| **bluepy** | ⭐⭐⭐ | ⭐⭐ 较复杂 | 需编译 C 扩展 | ❌ 不推荐 |
| **pygatt** | ⭐⭐ | ⭐⭐⭐ 简单 | 年久失修 | ❌ 不推荐 |

### 3.3 最终选择: bleak

**理由:**
1. **原生 BlueZ 后端** — 直接使用系统的 BlueZ 协议栈，不做额外封装
2. **纯 Python + asyncio** — 无 C 编译依赖，部署简单
3. **活跃维护** — PyPI 月下载 100万+，RK3568 ARM64 直接 `pip install`
4. **API 清晰** — BLE GATT Server 几行代码即可创建 Service + Characteristic
5. **与现有架构兼容** — 蓝牙线程用 asyncio 事件循环，不影响主循环

**需要修改的文件:** `bluetooth_link_c.py` (用 bleak 重写 BLE 实现)

---

## 四、GPS 方案确认

### 4.1 硬件情况

- ⚠️ QSM368ZP-WF **无内置 GNSS 芯片**
- 需要外接 GNSS 模组，走 UART 或 USB

### 4.2 UART 串口映射

从 SDK 外设文档 Table 3 — UART 接口复用表:

| UART 编号 | 设备节点 | 默认功能 | GPS 可用性 |
|-----------|----------|----------|------------|
| UART0 | `/dev/ttyS0` | 调试控制台 | ❌ 保留 |
| UART2 | `/dev/ttyS2` | 空闲/可复用 | ✅ 推荐 |
| UART8 | `/dev/ttyS8` | 测试点(引脚22,24) | ✅ **SDK 示例使用** |
| UART9 | `/dev/ttyS9` | 空闲 | ✅ 可用 |

**推荐**: 使用 UART8 (`/dev/ttyS8`)，SDK 有现成测试程序，波特率推荐 9600 (GPS NMEA 标准)。

### 4.3 代码适配

现有 `gps_reader_a.py` 只需修改 `config.py`:

```python
GPS_SERIAL_PORT = "/dev/ttyS8"   # 原 /dev/ttyAMA0
GPS_BAUDRATE = 9600               # 不变 (NMEA 标准)
```

其余代码 **无需修改**，pyserial 兼容。

---

## 五、显示方案确认

### 5.1 HDMI 输出

- RK3568 支持 VP0/VP1 最大 4096×2304
- HDMI 通过 EDID 自动识别显示器分辨率
- pygame 通过 SDL2 直接使用 framebuffer/DRM

### 5.2 屏幕配置

```python
# config.py — 推荐配置
SCREEN_WIDTH = 1024
SCREEN_HEIGHT = 600
SCREEN_FPS = 30  # 骑行场景 30fps 足够
```

如果 HDMI 屏实际分辨率不同 (如 800×480)，只需改这两个参数。

---

## 六、电量检测方案

### 6.1 ADC 读取电池电压

SDK 提供 5 路 ADC: **ADC2, ADC4~7**

读取方法 (从 SDK 外设文档):
```bash
cat /sys/bus/iio/devices/iio:device0/in_voltage0_raw
# 电压 = ADC值 × (1.8 / 1024) V
```

Python 实现:
```python
def read_battery_voltage():
    with open("/sys/bus/iio/devices/iio:device0/in_voltage0_raw") as f:
        raw = int(f.read().strip())
    voltage = raw * 1.8 / 1024  # ADC 参考电压 1.8V
    return voltage
```

⚠️ 实际电池电压需通过分压电阻接入 ADC 通道，需确认硬件接线。

---

## 七、音频输出方案

### 7.1 ALSA 音频

- SDK 提供扬声器/听筒接口
- Linux ALSA 驱动已集成
- pyttsx3 在 Linux 上默认使用 espeak/festival (不够好)

**推荐改用 gTTS + pygame.mixer 离线音频播放:**
- gTTS 生成 mp3 文件 → pygame.mixer 播放
- 或者使用 espeak-ng (更轻量，音质一般)

这部分根据音质需求再定，`voice_alert_c.py` 目前用 pyttsx3 作为占位没问题。

---

## 八、需要修改的文件清单

| 文件 | 修改内容 | 优先级 |
|------|----------|--------|
| `config.py` | GPS 端口 `/dev/ttyS8`、确认 BLE UUID | 🔴 必改 |
| `bluetooth_link_c.py` | 用 **bleak** 重写 BLE GATT Server | 🔴 必改 |
| `gps_reader_a.py` | 确认端口后无需大改 | 🟢 微调 |
| `hud_display_c.py` | 确认分辨率、移除 FULLSCREEN(调试阶段) | 🟢 微调 |
| `main.py` | 添加 ADC 电池监测 | 🟡 可选 |

---

## 十、显示方案确认 ✅ — SONY ECX336C Micro OLED

| 项目 | 确认值 |
|------|--------|
| **型号** | SONY ECX336C 0.23" Micro OLED |
| **分辨率** | **640 × 400** (nHD+) |
| **峰值亮度** | 3000 cd/m² (户外可用) |
| **驱动板** | HDMI 数字高清驱动板, 3.3V~5V, 功耗约 0.3W |
| **光学镜头** | 自由曲面树脂镜头 (选配), 24°FOV, 3m=50寸等效, 出瞳20mm |
| **安装方式** | 头戴式 AR 眼镜/头盔 |

> ⚠️ **UI 注意事项**: 640×400 分辨率配光学放大，UI 需极简设计。文字要大，地图细节无法在 0.23" 面板上辨认。
> 建议: 方向箭头 + 距离 + 路名的大字报风格，而非详细地图。

## 十一、电池方案确认 ✅ — 3S 锂电池组

| 项目 | 确认值 |
|------|--------|
| **类型** | 3S 锂电池组 (3×18650 或 LiPo) |
| **满电电压** | 12.60V (4.20V/cell) |
| **标称电压** | 11.10V (3.70V/cell) |
| **截止电压** | 9.00V (3.00V/cell) |
| **ADC 通道** | ADC2 (`in_voltage0_raw`), 参考电压 1.8V |
| **分压比** | ~7.67:1 (100kΩ/15kΩ), 12.6V→1.50V safe |
| **供电架构** | 电池 12V → QSM368ZP DC-IN(10-12V) + 5V→HDMI驱动板 |

> 3S 电池直接给 QSM368ZP 供电 (DC 10-12V), 同时降压到 5V 给 HDMI 驱动板。

## 十二、待确认问题

| # | 问题 | 状态 | 影响 |
|---|------|------|------|
| 1 | GPS 模组型号 | ✅ **已确认 ATGM336H-5N** | config 已更新 |
| 2 | 屏幕型号/分辨率 | ✅ **已确认 ECX336C 640×400** | config 已更新, HUD UI 需重设计 |
| 3 | 电池规格 | ✅ **已确认 3S LiPo** | config 已更新, ADC 分压比待实测 |
| 4 | 电池分压电阻精确值 | 🟡 待确认 | 电量百分比精度 |
| 5 | 是否需要 NPU AI 功能 | 🟡 待确认 | 额外工作量 |

---

**三大硬件已确认 ✅✅✅ — HUD UI 需要针对 640×400 分辨率和头戴光学做重新设计。**
