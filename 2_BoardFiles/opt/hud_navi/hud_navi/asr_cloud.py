"""
云语音识别 (ASR) — 支持阿里云/百度/讯飞
替代原有的 Goertzel 模板匹配方案

提供商:
  - aliyun:  阿里云 NLS 一句话识别 (推荐, 中文识别最好)
  - baidu:   百度语音识别 REST API
  - iflytek: 讯飞开放平台 WebSocket API

用法:
  asr = CloudASR(provider="aliyun")
  text = asr.recognize_file("/tmp/voice_input.wav")
  if text:
      cmd = match_command(text)  # 文本 → 导航命令
"""

import os
import json
import time
import base64
import hashlib
import hmac
import uuid
import wave
import struct
import logging
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# ============================================================
# 命令匹配 — 云 ASR 返回文本 → 导航命令
# ============================================================

COMMAND_PATTERNS = {
    "去公司":     ["去公司", "公司", "上班", "去单位"],
    "回家":       ["回家", "回去", "到家", "回家去"],
    "取消导航":   ["取消", "取消导航", "停止导航", "关闭导航", "不去了", "算了"],
    "还有多远":   ["还有多远", "多远", "距离", "多久到", "什么时候到", "还要多久"],
    "开始导航":   ["开始导航", "开始", "导航", "启动导航", "出发", "走"],
    "继续导航":   ["继续", "继续导航", "继续走"],
    "减速":       ["减速", "慢点", "太快了", "超速"],
    "切换离线":   ["离线", "切换离线", "离线模式"],
}

PLACES_KEYWORDS = {
    "去公司": True,
    "回家": True,
}


def match_command(text: str) -> str:
    """云 ASR 识别文本 → 模糊匹配导航命令"""
    if not text:
        return ""
    text = text.strip().lower().replace(" ", "").replace("，", "").replace("。", "")
    best_cmd, best_len = "", 0
    for cmd, keywords in COMMAND_PATTERNS.items():
        for kw in keywords:
            if kw in text and len(kw) > best_len:
                best_cmd, best_len = cmd, len(kw)
    return best_cmd


# ============================================================
# 音频工具
# ============================================================

def read_wav_file(path: str) -> bytes:
    """读取 WAV 文件的原始 PCM 数据"""
    try:
        with wave.open(path, "rb") as wf:
            params = wf.getparams()
            audio_bytes = wf.readframes(wf.getnframes())
            return audio_bytes, params.framerate, params.nchannels
    except Exception as e:
        logger.error(f"读取音频文件失败: {e}")
        return None, 16000, 1


def wav_to_pcm16(wav_path: str) -> bytes:
    """WAV 文件 → PCM16 原始字节 (单声道, 16kHz)"""
    audio, rate, ch = read_wav_file(wav_path)
    if audio is None:
        return None
    # 如果双声道, 转单声道 (取左声道)
    if ch == 2:
        mono = bytearray()
        for i in range(0, len(audio), 4):
            mono.extend(audio[i:i+2])
        audio = bytes(mono)
    return audio, rate


# ============================================================
# 阿里云 NLS 一句话识别
# ============================================================

class AliyunASR:
    """阿里云智能语音交互 — 一句话识别 REST API"""

    # REST API endpoint
    URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr"

    def __init__(self, appkey: str, access_key: str, access_secret: str):
        self.appkey = appkey
        self.access_key = access_key
        self.access_secret = access_secret

    def _sign(self) -> str:
        """生成阿里云 HMAC-SHA1 签名 (REST API 鉴权)"""
        # 阿里云 REST API 鉴权: 在 URL 参数中带上签名
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        # 构造签名字符串
        sign_str = f"{self.access_key}:{ts}:{nonce}"
        signature = hmac.new(
            self.access_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha1
        ).digest()
        signature = base64.b64encode(signature).decode("utf-8")
        return ts, nonce, signature

    def recognize(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        阿里云一句话识别
        audio_bytes: PCM16 单声道音频数据
        sample_rate: 采样率 (8000 或 16000)
        返回: 识别文本, 失败返回 ""
        """
        if not all([self.appkey, self.access_key, self.access_secret]):
            logger.warning("阿里云 ASR: 缺少 API 密钥, 请在 config.py 中配置")
            return ""

        ts, nonce, sig = self._sign()

        # 构造阿里云 NLS REST API URL
        params = {
            "appkey": self.appkey,
            "format": "pcm",
            "sample_rate": str(sample_rate),
            "enable_intermediate_result": "false",
            "enable_punctuation_prediction": "true",
            "enable_inverse_text_normalization": "true",
        }
        url = f"{self.URL}?{urlencode(params)}"

        headers = {
            "X-NLS-Token": f"{self.access_key}:{ts}:{nonce}:{sig}",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(audio_bytes)),
        }

        try:
            req = Request(url, data=audio_bytes, headers=headers, method="POST")
            resp = urlopen(req, timeout=10)
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("status") == 20000000:
                text = result.get("result", "")
                logger.info(f"阿里云 ASR: {text}")
                return text
            else:
                logger.warning(f"阿里云 ASR 错误: {result.get('status')} {result.get('status_text', '')}")
                return ""
        except URLError as e:
            logger.error(f"阿里云 ASR 网络错误: {e}")
            return ""
        except Exception as e:
            logger.error(f"阿里云 ASR 异常: {e}")
            return ""


# ============================================================
# 百度语音识别
# ============================================================

class BaiduASR:
    """百度语音识别 — REST API (短语音识别)"""

    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    ASR_URL = "https://vop.baidu.com/server_api"

    def __init__(self, app_id: str, api_key: str, secret_key: str):
        self.app_id = app_id
        self.api_key = api_key
        self.secret_key = secret_key
        self._token = None
        self._token_expire = 0

    def _get_token(self) -> str:
        """获取百度 access_token (有效期约30天)"""
        if self._token and time.time() < self._token_expire:
            return self._token

        params = urlencode({
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        })
        url = f"{self.TOKEN_URL}?{params}"

        try:
            resp = urlopen(url, timeout=5)
            data = json.loads(resp.read().decode("utf-8"))
            self._token = data.get("access_token", "")
            self._token_expire = time.time() + data.get("expires_in", 2592000) - 3600
            return self._token
        except Exception as e:
            logger.error(f"百度 ASR 获取 token 失败: {e}")
            return ""

    def recognize(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        百度语音识别
        audio_bytes: PCM16 单声道音频数据
        sample_rate: 采样率 (8000 或 16000)
        返回: 识别文本, 失败返回 ""
        """
        token = self._get_token()
        if not token:
            return ""
        if not self.api_key:
            logger.warning("百度 ASR: 缺少 API 密钥")
            return ""

        # 构造请求
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
        req_data = json.dumps({
            "format": "pcm",
            "rate": sample_rate,
            "channel": 1,
            "cuid": self.app_id or "hud_navi",
            "token": token,
            "speech": audio_base64,
            "len": len(audio_bytes),
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}

        try:
            req = Request(self.ASR_URL, data=req_data, headers=headers)
            resp = urlopen(req, timeout=10)
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("err_no") == 0:
                text = "".join(result.get("result", []))
                logger.info(f"百度 ASR: {text}")
                return text
            else:
                logger.warning(f"百度 ASR 错误: {result.get('err_no')} {result.get('err_msg', '')}")
                return ""
        except Exception as e:
            logger.error(f"百度 ASR 异常: {e}")
            return ""


# ============================================================
# 讯飞开放平台语音识别
# ============================================================

class IflytekASR:
    """讯飞语音识别 — REST API (一句话识别)"""

    URL = "https://iat-api.xfyun.cn/v2/iat"

    def __init__(self, app_id: str, api_key: str, api_secret: str):
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret

    def _build_url(self) -> str:
        """构造鉴权 URL (讯飞 REST API 鉴权)"""
        host = "iat-api.xfyun.cn"
        path = "/v2/iat"
        method = "POST"
        date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

        # 讯飞 HMAC-SHA256 签名
        signature_origin = f"host: {host}\ndate: {date}\n{method} {path} HTTP/1.1"
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            hashlib.sha256
        ).digest()
        signature_b64 = base64.b64encode(signature).decode("utf-8")

        authorization_origin = (
            f'api_key="{self.api_key}", '
            f'algorithm="hmac-sha256", '
            f'headers="host date request-line", '
            f'signature="{signature_b64}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")

        params = urlencode({
            "host": host,
            "date": date,
            "authorization": authorization,
        })
        return f"{self.URL}?{params}", host, date, authorization

    def recognize(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        讯飞语音识别
        audio_bytes: PCM16 单声道音频数据
        sample_rate: 采样率 (8000 或 16000)
        返回: 识别文本, 失败返回 ""
        """
        if not self.api_key:
            logger.warning("讯飞 ASR: 缺少 API 密钥")
            return ""

        url, host, date, auth = self._build_url()

        # 讯飞公共参数
        params = {
            "common": {
                "app_id": self.app_id,
            },
            "business": {
                "language": "zh_cn",
                "domain": "iat",
                "accent": "mandarin",
                "dwa": "wpgs",  # 动态修正
            },
            "data": {
                "status": 2,  # 最后一帧
                "format": f"audio/L16;rate={sample_rate}",
                "encoding": "raw",
                "audio": base64.b64encode(audio_bytes).decode("utf-8"),
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Host": host,
            "Date": date,
            "Authorization": auth,
        }

        try:
            req_data = json.dumps(params).encode("utf-8")
            req = Request(url, data=req_data, headers=headers, method="POST")
            resp = urlopen(req, timeout=10)
            result = json.loads(resp.read().decode("utf-8"))

            if result.get("code") == 0:
                # 解析识别结果
                text = ""
                if "data" in result and "result" in result["data"]:
                    ws = result["data"]["result"].get("ws", [])
                    for w in ws:
                        for cw in w.get("cw", []):
                            text += cw.get("w", "")
                if text:
                    # 去掉标点
                    text = text.replace("。", "").replace("，", "").strip()
                logger.info(f"讯飞 ASR: {text}")
                return text
            else:
                logger.warning(f"讯飞 ASR 错误: {result.get('code')} {result.get('message', '')}")
                return ""
        except Exception as e:
            logger.error(f"讯飞 ASR 异常: {e}")
            return ""


# ============================================================
# 统一的 CloudASR 入口
# ============================================================

class CloudASR:
    """云语音识别 — 统一接口"""

    PROVIDERS = ["aliyun", "baidu", "iflytek"]

    def __init__(self, provider: str = None):
        """初始化指定提供商的 ASR"""
        from config import (
            ASR_PROVIDER, ASR_ALIYUN_APPKEY, ASR_ALIYUN_ACCESS_KEY,
            ASR_ALIYUN_ACCESS_SECRET, ASR_BAIDU_APP_ID, ASR_BAIDU_API_KEY,
            ASR_BAIDU_SECRET_KEY, ASR_IFLYTEK_APP_ID, ASR_IFLYTEK_API_KEY,
            ASR_IFLYTEK_API_SECRET,
        )

        if provider is None:
            provider = ASR_PROVIDER

        self.provider = provider
        self._engine = None
        self._available = False

        if provider == "aliyun":
            if ASR_ALIYUN_APPKEY and ASR_ALIYUN_ACCESS_KEY:
                self._engine = AliyunASR(
                    ASR_ALIYUN_APPKEY,
                    ASR_ALIYUN_ACCESS_KEY,
                    ASR_ALIYUN_ACCESS_SECRET,
                )
                self._available = True
                logger.info("ASR: 阿里云 NLS 就绪")
            else:
                logger.warning("ASR: 阿里云未配置密钥, 请在 config.py 中设置")

        elif provider == "baidu":
            if ASR_BAIDU_API_KEY and ASR_BAIDU_SECRET_KEY:
                self._engine = BaiduASR(
                    ASR_BAIDU_APP_ID,
                    ASR_BAIDU_API_KEY,
                    ASR_BAIDU_SECRET_KEY,
                )
                self._available = True
                logger.info("ASR: 百度语音 就绪")
            else:
                logger.warning("ASR: 百度未配置密钥, 请在 config.py 中设置")

        elif provider == "iflytek":
            if ASR_IFLYTEK_API_KEY:
                self._engine = IflytekASR(
                    ASR_IFLYTEK_APP_ID,
                    ASR_IFLYTEK_API_KEY,
                    ASR_IFLYTEK_API_SECRET,
                )
                self._available = True
                logger.info("ASR: 讯飞 就绪")
            else:
                logger.warning("ASR: 讯飞未配置密钥, 请在 config.py 中设置")

        else:
            logger.warning(f"ASR: 未知提供商 '{provider}', 语音识别不可用")

    def is_available(self) -> bool:
        return self._available

    def recognize_file(self, wav_path: str) -> str:
        """
        从 WAV 文件识别语音
        返回: 识别文本, 失败返回 ""
        """
        if not self._available:
            return ""

        audio, rate = wav_to_pcm16(wav_path)
        if audio is None:
            return ""

        return self._engine.recognize(audio, rate)

    def recognize_bytes(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        从 PCM16 字节数据识别语音
        返回: 识别文本, 失败返回 ""
        """
        if not self._available:
            return ""
        return self._engine.recognize(audio_bytes, sample_rate)

    def recognize_and_match(self, wav_path: str) -> str:
        """
        录音 → ASR → 命令匹配 (一步到位)
        返回: 匹配到的命令, 失败返回 ""
        """
        text = self.recognize_file(wav_path)
        if not text:
            return ""
        cmd = match_command(text)
        if cmd:
            logger.info(f"云 ASR 识别: '{text}' → 命令: '{cmd}'")
        else:
            logger.info(f"云 ASR 识别: '{text}' → 未匹配任何命令")
        return cmd


# ============================================================
# 测试 (需要一个 WAV 文件)
# ============================================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    asr = CloudASR()
    if not asr.is_available():
        print("ASR 未配置, 请先在 config.py 中填写 API 密钥")
        print("  阿里云: ASR_ALIYUN_APPKEY / ASR_ALIYUN_ACCESS_KEY / ASR_ALIYUN_ACCESS_SECRET")
        print("  百度:   ASR_BAIDU_APP_ID / ASR_BAIDU_API_KEY / ASR_BAIDU_SECRET_KEY")
        print("  讯飞:   ASR_IFLYTEK_APP_ID / ASR_IFLYTEK_API_KEY / ASR_IFLYTEK_API_SECRET")
        sys.exit(1)

    if len(sys.argv) > 1:
        wav = sys.argv[1]
    else:
        wav = "/tmp/voice_input.wav"
        if not os.path.exists(wav):
            print(f"请提供 WAV 文件路径: python asr_cloud.py <文件.wav>")
            print("或先运行 voice_command.py 录制测试音频")
            sys.exit(1)

    print(f"识别文件: {wav}")
    cmd = asr.recognize_and_match(wav)
    print(f"命令: {cmd or '(未识别)'}")
