"""
截图查证 · 腾讯云 OCR 客户端
- live 模式：调用腾讯云 OCR。优先用高精度版（GeneralAccurateOCR，用户已开通），
  失败则回退通用印刷体（GeneralBasicOCR），复用 config 里的
  WSA_SECRET_ID / WSA_SECRET_KEY（与元宝搜索、混元同属一个腾讯云账号，无需新凭证）。
- 离线 / 无密钥 / 未开通 OCR 服务：返回 ok=False，由上层提示用户手动粘贴截图文字。
"""
import base64
import io

from config import WSA_SECRET_ID, WSA_SECRET_KEY


def _compress(image_bytes, max_edge=2000, quality=82):
    """压缩大图：降分辨率 + 转 JPEG，减小 base64 体积、提速、提升识别稳定性。失败则原样返回。"""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_edge:
            scale = max_edge / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return image_bytes


def ocr_image_bytes(image_bytes):
    """识别图片中的文字。返回 {ok: bool, text: str, error: str}。"""
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
        from tencentcloud.ocr.v20181119 import ocr_client, models
    except ImportError:
        return {"ok": False, "error": "腾讯云 OCR SDK 未安装（pip install tencentcloud-sdk-python 即可）"}

    if not (WSA_SECRET_ID and WSA_SECRET_KEY):
        return {"ok": False, "error": "未配置腾讯云密钥（WSA_SECRET_ID / WSA_SECRET_KEY）"}

    try:
        data = _compress(image_bytes)
        b64 = base64.b64encode(data).decode("utf-8")
        cred = credential.Credential(WSA_SECRET_ID, WSA_SECRET_KEY)
        client = ocr_client.OcrClient(cred, "ap-guangzhou")
        # 优先高精度版（用户已开通），失败回退通用基础版
        engines = [
            ("GeneralAccurateOCR", models.GeneralAccurateOCRRequest),
            ("GeneralBasicOCR", models.GeneralBasicOCRRequest),
        ]
        resp = None
        last_err = None
        for method, req_cls in engines:
            try:
                req = req_cls()
                req.ImageBase64 = b64
                resp = getattr(client, method)(req)
                break
            except TencentCloudSDKException as e:
                last_err = e
                # 未开通/鉴权类错误才尝试下一个引擎；限频等直接抛出
                if "UnOpen" in str(e) or "AuthFailure" in str(e) or "ResourceNotFound" in str(e):
                    continue
                raise
        if resp is None:
            return {"ok": False, "error": "腾讯云 OCR 调用失败：" + str(last_err)}
        texts = []
        for t in (resp.TextDetections or []):
            txt = getattr(t, "DetectedText", None) or ""
            if txt.strip():
                texts.append(txt.strip())
        if not texts:
            return {"ok": False, "error": "图片中未识别出文字（可能是纯图片/表情包）"}
        return {"ok": True, "text": "\n".join(texts)}
    except TencentCloudSDKException as e:
        # 常见：未开通 OCR 服务（ResourceNotFound / AuthFailure）、余额不足、限频
        return {"ok": False, "error": "腾讯云 OCR 调用失败：" + str(e)}
    except Exception as e:
        return {"ok": False, "error": "OCR 处理异常：" + str(e)}
