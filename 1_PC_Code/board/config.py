"""
全局配置文件
所有可调参数集中管理，便于适配不同硬件环境
"""

# ============================================================
# 屏幕配置 — SONY ECX336C 0.23" Micro OLED + HDMI驱动板
# ============================================================
# 物理分辨率: 640×400, 峰值亮度3000cd/m²
# 驱动板: HDMI数字高清驱动板, 3.3V~5V供电, 功耗约0.3W(含屏)
# 光学: 选配自由曲面镜头, 24°FOV, 3m等效50寸, 出瞳距离20mm
# 场景: 头戴式AR骑行导航
SCREEN_WIDTH = 640
SCREEN_HEIGHT = 400
SCREEN_FPS = 30  # HUD刷新帧率, OLED响应速度快可降到15省电

# ============================================================
# GPS 配置 — ATGM336H-5N-31 (中科微, GPS+北斗双模)
# ============================================================
# 接线: GPS TX → 主控 RX, GPS RX → 主控 TX, 3.3V供电
# 协议: NMEA 0183, 上电自动输出, 无需AT指令激活
# 输出语句: $GNRMC / $GNGGA / $GNGSV (GN=双模GNSS前缀)
# 冷启动搜星: 约32秒
GPS_VENDOR = "ATGM336H-5N"          # 中科微 GPS+北斗双模模组
GPS_SERIAL_PORT = "/dev/ttyS8"      # QSM368ZP UART8 (J1201 pin22+24)
GPS_BAUDRATE = 9600                 # NMEA 标准波特率
GPS_TIMEOUT = 1.0                   # 串口读超时(秒)
GPS_COLD_START_SEC = 35             # 冷启动搜星等待时间

# ============================================================
# 蓝牙 BLE 配置
# ============================================================
BLE_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
BLE_CHAR_UUID_RX = "0000fff1-0000-1000-8000-00805f9b34fb"  # 手机→HUD(写)
BLE_CHAR_UUID_TX = "0000fff2-0000-1000-8000-00805f9b34fb"  # HUD→手机(通知)
BLE_DEVICE_NAME = "HUD-Navi"

# ============================================================
# 高德地图 API 配置
# ============================================================
AMAP_API_KEY = "6af97f35f48f772b1532efe395099ffb"

# ============================================================
# 路径规划配置
# ============================================================
ROUTE_MODE = "auto"  # "online" / "offline" / "auto"(在线优先,失败降级)
OFF_ROUTE_THRESHOLD_M = 30  # 偏航判定距离(米)
OFF_ROUTE_CONSECUTIVE_COUNT = 3  # 连续偏航次数才触发重规划(防GPS漂移)
DESTINATION_ARRIVED_THRESHOLD_M = 50  # 到达目的地判定距离(米)
VOICE_ALERT_DISTANCE_M = 100  # 距离路口多少米时播报

# ============================================================
# 离线地图配置
# ============================================================
OSM_MAP_FILE = "data/map.osm"        # 离线OSM地图文件路径
OFFLINE_TILE_DIR = "data/tiles/"      # 离线瓦片图目录
NETWORK_CHECK_URL = "restapi.amap.com"  # 网络连通检测目标
NETWORK_CHECK_TIMEOUT = 3             # 检测超时(秒)
NETWORK_CHECK_INTERVAL = 30           # 定期检测间隔(秒)

# ============================================================
# 语音配置
# ============================================================
TTS_RATE = 180  # 语速(词/分钟)
TTS_VOLUME = 0.8  # 音量(0.0~1.0)

# ============================================================
# 系统监测
# ============================================================
# 电池: 3S 锂电池组 (3×18650 或 3S LiPo)
#   满电: 4.20V × 3 = 12.60V
#   标称: 3.70V × 3 = 11.10V
#   低压: 3.00V × 3 =  9.00V (安全截止)
# ADC: QSM368ZP SAR-ADC, 参考电压1.8V, 10位(0-1023)
#   分压比: R_top / R_bottom = 100kΩ / 15kΩ ≈ 7.67:1
#   12.6V → 1.50V (ADC=853),  9.0V → 1.07V (ADC=609)
#   默认ADC通道: ADC2 (in_voltage0_raw)
BATTERY_CELLS = 3
BATTERY_FULL_V = 12.6       # 满电电压
BATTERY_NOMINAL_V = 11.1    # 标称电压
BATTERY_CUTOFF_V = 9.0      # 安全截止电压
BATTERY_VOLTAGE_DIVIDER = 7.67  # 分压比 (R1+R2)/R2
BATTERY_ADC_CHANNEL = "in_voltage0_raw"  # ADC2 通道
LOW_BATTERY_THRESHOLD = 15  # 低电量警告阈值(%)
WEAK_GPS_SIGNAL_THRESHOLD = 3  # GPS卫星数低于此值视为弱信号
TTS_EDGE_VOICE = "zh-CN-XiaoxiaoNeural"  # Edge-TTS 中文语音

# ============================================================
# 传感器融合配置 (sensor_fusion.py)
# ============================================================
FUSION_OUTPUT_HZ = 30               # 融合输出频率
FUSION_GPS_WEIGHT = 0.8             # GPS测量权重 (0-1)
FUSION_IMU_WEIGHT = 0.3             # IMU预测权重 (0-1)
FUSION_VO_WEIGHT = 0.5              # 视觉里程计权重 (0-1)
FUSION_DEAD_RECKONING_MAX_SEC = 60  # 纯推测导航最大持续时间
FUSION_DEAD_RECKONING_DRIFT_M = 50  # 推测导航漂移阈值(米)
FUSION_PROCESS_NOISE_POS = 5e-6     # 位置过程噪声 (弧度)
FUSION_PROCESS_NOISE_HDG = 0.01     # 航向过程噪声 (弧度)
FUSION_PROCESS_NOISE_SPD = 0.5      # 速度过程噪声 (m/s)
FUSION_MEASUREMENT_NOISE_GPS = 5.0  # GPS测量噪声 (米)
FUSION_MEASUREMENT_NOISE_HDG = 10.0 # 航向测量噪声 (度)
FUSION_GPS_TIMEOUT_SEC = 5.0        # GPS超时判定(秒)
FUSION_VO_TIMEOUT_SEC = 3.0         # 视觉里程计超时(秒)

# ============================================================
# IMU 传感器配置 (MPU6050/ICM20948)
# ============================================================
IMU_I2C_BUS = 1                     # I2C总线编号
IMU_I2C_ADDR = 0x68                 # MPU6050 默认I2C地址
IMU_SAMPLE_RATE = 100               # IMU采样率(Hz)
IMU_ACCEL_RANGE = 8                 # 加速度计量程(g): 2/4/8/16
IMU_GYRO_RANGE_DPS = 500            # 陀螺仪量程(dps): 250/500/1000/2000
