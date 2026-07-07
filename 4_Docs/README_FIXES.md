# 板子实测问题修复

> 测试环境: QSM368ZP-WF, Python 3.13.13 (Anaconda), NPU驱动 v1.4.0

---

## 问题 1: 模型版本不兼容 ✅ 已修复

**现象:**
```
E RKNN: Invalid RKNN model version 6
rknn_init fail! ret=-1
```

**原因:** 板子 NPU 驱动 `librknnrt.so` v1.4.0 (2022) 不支持 toolkit v2.3.2 生成的 version 6 模型

**修复:** 已提取并放入 `runtime/librknnrt.so` (v2.3.2, 7.37 MB)

```bash
# 在板子上:
# 1. 备份旧驱动
cp /usr/lib/librknnrt.so /usr/lib/librknnrt.so.v1.4.0.bak

# 2. 替换为新驱动
cp /opt/hud_navi/runtime/librknnrt.so /usr/lib/librknnrt.so

# 3. 验证
strings /usr/lib/librknnrt.so | grep "librknnrt version"
# 应输出: librknnrt version: 2.3.2

# 4. 测试 Whisper
cd /data/whisper
./rknn_whisper_demo ./model/whisper_encoder_base_20s.rknn \
                    ./model/whisper_decoder_base_20s.rknn \
                    zh ./model/test_zh.wav
```

---

## 问题 2: Python 3.13 装不了 cp312 wheel

**现象:** `pip3 install rknn_toolkit_lite2-*-cp312-*.whl` 失败

**原因:** 板子 Python 3.13.13，wheel 最高只到 cp312 (Python 3.12)

**方案 A (推荐):** 用 C 二进制路径，不需要 Python
- `rknn_whisper_demo` 是 C 编译的，直接调用 librknnrt.so
- 更新 NPU 驱动后就能用新版模型

**方案 B:** 装 Python 3.12 环境
```bash
# 用 pyenv 或 conda
conda create -n py312 python=3.12 -y
conda activate py312
pip install packages/rknn_toolkit_lite2-2.3.2-cp312-cp312-linux_aarch64.whl
python3 -c "from rknnlite.api import RKNNLite; print('OK')"
```

**方案 C:** 强制安装试试 (Anaconda Python 有时兼容)
```bash
pip install --force-reinstall --no-deps \
  packages/rknn_toolkit_lite2-2.3.2-cp312-cp312-linux_aarch64.whl
```

---

## 问题 3: YOLO 模型

YOLOv5s 模型同样是 version 6，也需要更新 NPU 驱动后才能加载。

如果板子上已有能用的 YOLO C 程序:
```bash
cd /data/yolo
./rknn_yolov5_demo ./model/yolov5s-640-640.rknn
```

---

## 文件清单更新

```
AI_语音交付包/
├── models/              ← 无需改动，已经是 version 6
├── runtime/             ← 新增!
│   └── librknnrt.so     ← NPU驱动 v2.3.2 (替换 /usr/lib/librknnrt.so)
├── packages/            ← rknn-lite2 .whl (作备用)
├── code/                ← Python代码
└── 修复说明.md           ← 本文件
```
