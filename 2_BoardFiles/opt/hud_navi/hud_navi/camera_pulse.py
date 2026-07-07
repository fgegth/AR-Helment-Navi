"""
摄像头AI检测 — YOLOv5n RKNN + NMS去重
RKNNLite Python API, 不抢RGA, 每秒1帧
"""
import sys, os, time, logging, subprocess
import numpy as np
sys.path.insert(0, '/opt/hud_navi')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [Pulse] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('Pulse')

MODEL = '/data/yolo/model/yolov5s-640-640.rknn'
DEV = '/dev/video23'
IMG_W, IMG_H = 640, 640
CONF_THRESH = 0.3; NMS_THRESH = 0.45
VEHICLE_CLASSES = {0, 1, 2, 3, 5, 7}
COCO = {0:'人',1:'自行车',2:'汽车',3:'摩托',5:'公交',7:'卡车'}

# ── NMS ──
def nms(boxes, scores, thresh):
    if len(boxes) == 0: return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while len(order) > 0:
        i = order[0]; keep.append(i)
        if len(order) == 1: break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1); h = np.maximum(0.0, yy2 - yy1)
        iou = (w * h) / (areas[i] + areas[order[1:]] - w * h)
        order = order[np.where(iou <= thresh)[0] + 1]
    return keep

# ── NPU引擎 ──
class NPU:
    def __init__(self):
        self.rknn = None
    def init(self):
        from rknnlite.api import RKNNLite
        self.rknn = RKNNLite()
        self.rknn.load_rknn(MODEL)
        self.rknn.init_runtime()
        dummy = np.random.randint(0, 256, (IMG_H, IMG_W, 3), dtype=np.uint8)
        self.infer(dummy)
        logger.info('NPU就绪 (YOLOv5n)')
    def infer(self, img):
        if img.shape[:2] != (IMG_H, IMG_W):
            from PIL import Image
            img = np.array(Image.fromarray(img).resize((IMG_W, IMG_H), Image.LANCZOS))
        img = np.flipud(img)  # 摄像头倒装
        inp = np.expand_dims(img, axis=0).astype(np.uint8)
        outs = self.rknn.inference(inputs=[inp], data_format=['nhwc'])
        det = outs[0][0]  # [25200, 85]
        boxes = det[:, :4]  # cx,cy,w,h
        score = det[:, 4]   # 合并分数
        cls_id = np.argmax(det[:, 5:], axis=1)
        # cx,cy,w,h → x1,y1,x2,y2
        boxes[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        boxes[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        boxes[:, 2] = boxes[:, 0] + boxes[:, 2]
        boxes[:, 3] = boxes[:, 1] + boxes[:, 3]
        # 按类别NMS
        results = []
        for cls in np.unique(cls_id):
            mask = (cls_id == cls) & (score > CONF_THRESH)
            if not mask.any(): continue
            idx = np.where(mask)[0]
            keep = nms(boxes[idx], score[idx], NMS_THRESH)
            for k in keep:
                j = idx[k]
                results.append({'cls': int(cls_id[j]), 'conf': float(score[j]),
                    'x1': boxes[j,0], 'y1': boxes[j,1], 'x2': boxes[j,2], 'y2': boxes[j,3]})
        return results
    def close(self):
        if self.rknn:
            try: self.rknn.release()
            except: pass

logger.info('AI检测启动 (YOLOv5n+NMS)')

# 外层保活: NPU崩溃/进程被杀自动重启
while True:
    try:
        npu = NPU()
        npu.init()
    except Exception as e:
        logger.error(f'NPU失败({e}), 3秒重试...')
        time.sleep(3)
        continue

    # 主检测循环
    while True:
        t0 = time.time()
        try:
            subprocess.run(['v4l2-ctl','-d',DEV,'--set-fmt-video',
                'width=640,height=480,pixelformat=MJPG'],capture_output=True,timeout=3)
            subprocess.run(['v4l2-ctl','-d',DEV,'--stream-mmap','--stream-count=1',
                '--stream-to=/tmp/pulse.jpg'],capture_output=True,timeout=3)
        except Exception:
            time.sleep(0.5)
            continue
        t1 = time.time()

        try:
            from PIL import Image
            img = np.array(Image.open('/tmp/pulse.jpg').convert('RGB'))[:,:,::-1]  # BGR
        except Exception:
            time.sleep(0.5)
            continue
        t2 = time.time()

        try:
            dets = npu.infer(img)
        except Exception as e:
            logger.error(f'NPU推理异常: {e}')
            break  # 跳出内循环, 重新初始化NPU
        t3 = time.time()

        vehicles = [d for d in dets if d['cls'] in (2, 5, 7)]
        # 跨进程通信: 写文件给main.py读取
        import json as _json
        alert = {'level': 0, 'msg': '', 'vehicles': 0}
        if vehicles:
            best = max(vehicles, key=lambda d: d['conf'])
            alert['level'] = 2 if best['conf'] > 0.5 else 1
            alert['msg'] = f'后方{len(vehicles)}辆车'
            alert['vehicles'] = len(vehicles)
        with open('/tmp/camera_alert.json', 'w') as f:
            _json.dump(alert, f)

        elapsed = time.time() - t0
        top = sorted(dets, key=lambda d: -d['conf'])[:3]
        cls_info = []
        for d in top:
            cn = COCO.get(d['cls'], 'cls%d' % d['cls'])
            cls_info.append('%s:%.2f' % (cn, d['conf']))
        names = ','.join(cls_info)
        logger.info('%s (抓%.0fms PIL%.0fms NPU%.0fms) %s' % (
            f'⚠ x{len(vehicles)}' if vehicles else '无',
            (t1-t0)*1000, (t2-t1)*1000, (t3-t2)*1000, names))

        time.sleep(max(0.1, 1.0 - (time.time() - t0)))
