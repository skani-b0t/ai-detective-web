"""
AI 审讯室 · 元宝搜索查证工具  ——  Flask 入口
运行：
  1) pip install -r requirements.txt
  2) cp .env.example .env   （offline 模式无需填 Key 即可演示）
  3) python app.py
访问 http://127.0.0.1:5000
"""
import json
import os
import threading
import time
from collections import defaultdict, deque
from flask import Flask, request, render_template, jsonify
from verifier import verify_text, fuzzy_find
from ocr_client import ocr_image_bytes
from claim_extractor import clean_ocr_text
import cache as report_cache

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# 公网部署时，请求会先经过托管平台的 HTTP 反向代理。
# 不修这一层，request.remote_addr 永远是代理的内网地址，
# 限流就会把「所有访客」当成同一个人（第 12 次查询后全站 429）。
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Public deployments share a finite search allowance. Count only requests that
# can spend API calls; one worker is configured in render.yaml.
_usage = defaultdict(deque)
_total_usage = deque()
_usage_lock = threading.Lock()


def _search_budget():
    if os.getenv("PUBLIC_RATE_LIMIT", "false").lower() != "true":
        return None
    now = time.time()
    window = 24 * 60 * 60
    client = request.remote_addr or "unknown"
    per_client = max(1, int(os.getenv("SEARCHES_PER_CLIENT_DAY", "12")))
    daily_total = max(1, int(os.getenv("SEARCHES_TOTAL_DAY", "200")))
    with _usage_lock:
        client_usage = _usage[client]
        while client_usage and client_usage[0] < now - window:
            client_usage.popleft()
        while _total_usage and _total_usage[0] < now - window:
            _total_usage.popleft()
        if len(client_usage) >= per_client or len(_total_usage) >= daily_total:
            response = jsonify({"error": "今天的查询额度已用完，请明天再试。", "code": "RATE_LIMITED"})
            response.headers["Retry-After"] = "3600"
            return response, 429
        client_usage.append(now)
        _total_usage.append(now)
    return None

# ② 人在回路：用户人工复核（改判）的内存存储（演示用，重启即清空）
_overrides = {}
_overrides_lock = threading.Lock()


@app.route("/override", methods=["POST"])
def override():
    """记录一条人工复核（人在回路）。body: {claim, verdict, note}。返回累计条数。"""
    data = request.get_json(silent=True) or {}
    claim = (data.get("claim") or "").strip()
    verdict = (data.get("verdict") or "").strip()
    note = (data.get("note") or "").strip()
    if not claim or not verdict:
        return jsonify({"error": "claim 与 verdict 必填"}), 400
    with _overrides_lock:
        _overrides[claim] = {"verdict": verdict, "note": note}
        count = len(_overrides)
    return jsonify({"ok": True, "count": count})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    from config import WSA_MODE, WSA_SECRET_ID, WSA_SECRET_KEY, TOKENHUB_API_KEY
    return jsonify({
        "search_mode": WSA_MODE,
        "search_ready": WSA_MODE == "live" and bool(WSA_SECRET_ID and WSA_SECRET_KEY)
                        and not WSA_SECRET_ID.startswith("填入")
                        and not WSA_SECRET_KEY.startswith("填入"),
        "model_ready": bool(TOKENHUB_API_KEY),
    })


@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    scene = data.get("scene", "rumor")  # ai | rumor
    use_llm = bool(data.get("use_llm", True))  # 默认走混元抽取，失败自动回退启发式
    if not text:
        return jsonify({"error": "请输入要查证的内容"}), 400
    if len(text) > 2000:
        return jsonify({"error": "一次最多输入 2000 字"}), 400

    # 缓存：演练过的示例秒回（现场演示避免长时间等待）；截图路径不走缓存
    from_screenshot = bool(data.get("from_screenshot") and data.get("ocr_text"))
    if not from_screenshot:
        hit = report_cache.get(scene, text)
        if hit:
            return jsonify(hit)

    limited = _search_budget()
    if limited:
        return limited

    if scene == "fuzzy":  # 查模糊：线索检索模式，只摆证据不下结论
        try:
            report = fuzzy_find(text, use_llm=use_llm)
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "code": "SEARCH_NOT_READY"}), 503
        report_cache.put(scene, text, report)
        return jsonify(report)
    try:
        report = verify_text(text, scene=scene, use_llm=use_llm)
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "SEARCH_NOT_READY"}), 503
    if not from_screenshot:
        report_cache.put(scene, text, report)
    # 截图查证：把「来自截图」的识别文字与流水线第 0 步透传回前端
    if data.get("from_screenshot") and data.get("ocr_text"):
        was_cleaned = bool(data.get("ocr_cleaned"))
        report["ocr_text"] = data["ocr_text"]
        report["ocr_cleaned"] = was_cleaned
        if was_cleaned:
            detail = ("从上传截图 OCR 识别，混元 hy3 提炼核心断言 %d 字，进入九阶段查证流水线"
                      % len(data["ocr_text"]))
            engine = "腾讯云OCR + hy3"
        else:
            detail = "从上传截图识别 %d 字，进入九阶段查证流水线" % len(data["ocr_text"])
            engine = "腾讯云OCR"
        report["pipeline"].insert(0, {
            "stage": "0 · 截图文字提取",
            "detail": detail,
            "engine": engine,
        })
    return jsonify(report)


@app.route("/ocr", methods=["POST"])
def ocr():
    """截图查证第一步：接收上传图片，腾讯云 OCR 提取文字。返回 {text} 或 {error, hint}。"""
    f = request.files.get("file")
    limited = _search_budget()
    if limited:
        return limited
    if not f:
        return jsonify({"error": "未收到图片文件"}), 400
    raw = f.read()
    if not raw:
        return jsonify({"error": "图片为空"}), 400
    try:
        r = ocr_image_bytes(raw)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": "识别失败：" + str(e)}), 500
    if not r.get("ok"):
        return jsonify({
            "error": r.get("error", "识别失败"),
            "hint": "自动识别需开通腾讯云「通用文字识别 OCR」服务（免费额度即可）。"
                    "也可手动把截图里的文字粘贴到输入框，照常查证。",
        }), 422
    # OCR 原文含大量界面杂讯（账号名/点赞数/按钮等），先用混元 hy3 提炼核心断言再回传
    cleaned, method = clean_ocr_text(r["text"])
    return jsonify({"text": cleaned, "raw_text": r["text"], "clean_method": method})


# 内置示例文本（与前端「一键示例」一致；warmup.py 复用此常量做赛前预热）
DEMO_TEXTS = {
    "ai": ("根据最新研究，每天喝白酒可以杀死幽门螺杆菌，预防胃癌。"
           "XX大学2026年录取分数线为680分，排名全国前五。"
           # 第三条特意设计成「可计算的矛盾」：80万→200万实为 +150%，而非宣称的 300%，
           # 代码核验能算出「声明值 vs 实测值」的差距并自动降级判定（演示 4 用这条）。
           "某品牌宣称产品销量同比增长300%，从去年80万件增至今年200万件。"),
    "rumor": ("紧急通知：喝白酒能杀死幽门螺杆菌，每天二两保胃健康！"
              "某地已确诊一种新病毒，传播极快，吃大蒜可预防，大家转发提醒家人。"),
    "latest": ("今天上海最高气温24℃，昼夜温差较大。"
               "周杰伦2026年巡回演唱会上海站已获批举办。"),
    "fuzzy": "那个讲法医的女主剧叫什么名字？",
}


@app.route("/demo/<scene>")
def demo(scene="rumor"):
    """内置示例，方便无准备时一键演示。"""
    text = DEMO_TEXTS.get(scene) or DEMO_TEXTS["ai"]
    # 缓存：内置示例预热后秒回（warmup.py 赛前跑一次即可）
    hit = report_cache.get(scene, text)
    if hit:
        return jsonify(hit)
    limited = _search_budget()
    if limited:
        return limited
    if scene == "fuzzy":
        out = fuzzy_find(text)
    else:
        out = verify_text(text, scene=scene)
    report_cache.put(scene, text, out)
    return jsonify(out)


if __name__ == "__main__":
    import os as _os
    _port = int(_os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=_port, debug=False)
