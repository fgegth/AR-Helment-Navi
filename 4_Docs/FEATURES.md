# HUD AR 骑行导航 — 全部功能清单

---

## ✅ 基础功能 (V1.0 已实现)

### 通信与连接
- [x] WiFi自动配网 (首次手机连热点输密码，之后开机自连)
- [x] HTTP REST API (手机↔板子通信)
- [x] BLE蓝牙通信 (GATT Server, 备用通道)
- [x] 开机自启 (BusyBox init + start-stop-daemon看门狗)
- [x] 崩溃自动重启 (看门狗, 10秒复活)

### 导航核心
- [x] GPS定位 (ATGM336H-5N, GPS+北斗双模)
- [x] 高德驾车路线规划 (Turn-by-Turn, 单路线输出)
- [x] 偏航检测与自动重规划
- [x] 到达目的地判定
- [x] 离线OSM路径规划 (A*算法, 需.osm地图文件)

### HUD显示
- [x] 纯箭头指引模式 (↑←→↩, 距离三级可视化)
- [x] 距离+路名AR大字报
- [x] 椭圆光学遮罩 (模拟24°FOV)
- [x] 昼夜模式切换 (按N键)
- [x] 到达/GPS丢失/空闲 场景渲染

### 语音交互
- [x] 语音播报 (gTTS在线 → WAV预录人声 → ALSA蜂鸣 三级降级)
- [x] 路口100米内自动播报
- [x] 防重复播报 (5秒内同内容不重复)

### 手机App
- [x] 高德地图JS API 动态地图显示
- [x] 地址搜索 (高德geocode)
- [x] 路线预览 (步行路径画线)
- [x] 目的地发送 (POST /destination)
- [x] 连接状态指示
- [x] 常用地点管理
- [x] 骑行统计 (次数/里程/时长)
- [x] 数据分析 (速度分布/个人记录/热门目的地)
- [x] IP手动配置
- [x] 板子远程控制 (重启/关机/语音录入/AI启动)

### 硬件适配
- [x] SONY ECX336C 640×400 Micro OLED
- [x] 3S锂电池 ADC电量监测
- [x] ATGM336H-5N GPS模组 UART8
- [x] HDMI驱动板输出

---

## 🟡 拓展功能 (代码已写，待启用)

### AI智能检测
| 文件 | 功能 | 依赖 |
|------|------|------|
| `ai_detect.py` | 后方车辆检测 (YOLOv5 NPU) | USB摄像头 + RKNN模型 |
| `ai_alert.py` | AI预警管理 (观察/警告/危险) | ai_detect.py |
| `camera_capture.py` | USB摄像头采集 (ffmpeg/v4l2) | USB摄像头 |

### 语音控制 (✅ 已录制, 真人语音包完整)
| 文件 | 功能 | 依赖 |
|------|------|------|
| `voice_command.py` | 语音命令识别 (声纹+模板匹配+Whisper三层) | 麦克风 |
| `voice_auth.py` | 声纹验证 (MFCC特征+余弦相似度) | 麦克风 |
| `whisper_asr.py` | Whisper语音识别 (RKNN NPU推理) | RKNN模型文件 |
| `data/commands.json` | **5条语音命令训练数据** (去公司/回家/取消/多远/开始) | — |
| `data/voiceprint.json` | **声纹模板** (机主声纹特征已录入) | — |
| `data/voice_prompts/*.wav` | **14个真人预录WAV** (离线TTS人声) | — |

语音提示音清单：
| 文件 | 内容 | 大小 |
|------|------|------|
| cmd_qugongsi.wav | "去公司" | 89KB |
| cmd_huijia.wav | "回家" | 84KB |
| cmd_kaishi.wav | "开始导航" | 103KB |
| cmd_quxiao.wav | "取消" | 104KB |
| cmd_duoyuan.wav | "还有多远" | 99KB |
| cmd_ok.wav | "识别成功" | 78KB |
| cmd_fail.wav | "未识别" | 121KB |
| start_speak.wav | "开始说话" | 69KB |
| vp_pass.wav | "声纹通过" | 77KB |
| vp_fail.wav | "声纹失败" | 88KB |
| all_done.wav | "已完成" | 100KB |
| done.wav | "已完成" | 64KB |
| enroll_vp.wav | 声纹录入引导 | 163KB |

### 骑行智能
| 文件 | 功能 | 依赖 |
|------|------|------|
| `smart_features.py` | 常用地点自动学习+行车记录+超速提醒 | — |
| `ride_analysis.py` | 骑行数据深度分析 | smart_features.py |
| `road_safety.py` | 道路安全等级分析 (🟢🟡🔴) | — |
| `data/frequent_places.json` | **已记录5个真实目的地** (含使用次数) | — |

常用地点实测数据：
| 地点 | 次数 | 最近使用 |
|------|------|------|
| 公司 | 1217 | 2026-06-30 |
| 安徽建筑大学 | 3 | 2026-07-01 |
| 滨湖会展中心 | 2 | 2026-07-01 |
| 滨湖 | 1 | 2026-07-01 |
| 合肥工业大学 | 1 | 2026-07-01 |

### 通信扩展
| 文件 | 功能 | 依赖 |
|------|------|------|
| `gps_ws_server.py` | GPS数据WebSocket实时推送 | — |
| `bluetooth_spp.py` | 蓝牙SPP串口通信 (备用) | — |

---

## 🔮 未来可扩展

- [ ] 手机App自动发现板子 (mDNS/Bonjour)
- [ ] 手机App实时同步板子路线 (WebSocket)
- [ ] 多路线对比 (需高德多路线API)
- [ ] 电子眼/限速提醒
- [ ] 蓝牙来电/消息弹窗
- [ ] 行车记录仪 (摄像头+存储)
- [ ] 群组骑行 (多设备互联)
- [ ] 语音全程交互 (不需手机操作)
