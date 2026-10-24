"""
混元 (TokenHub) 统一调用封装
意图识别 / 查询改写 / 语义核验 / 报告生成 四个增强层共用同一个 chat 入口。

设计原则：
- 无 TOKENHUB_API_KEY 时所有函数一律返回 None，调用方据此回退到启发式，保证不崩。
- 接口走 OpenAI 兼容协议：https://tokenhub.tencentmaas.com/v1/chat/completions
- 模型由 config.HUNYUAN_MODEL 控制（本账号实测可用服务 ID 为 hy3）。
"""
import json
import re
import time
import threading
import urllib.request
import urllib.error
from config import TOKENHUB_API_KEY, HUNYUAN_MODEL

_ENDPOINT = "https://tokenhub.tencentmaas.com/v1/chat/completions"
_MAX_RETRY = 3
_MIN_INTERVAL = 2.0  # 全局最小调用间隔（秒），规避 TokenHub 限流空返回

# 全局限速锁：保证任意两次混元调用至少间隔 _MIN_INTERVAL，避免并发触发限流
_rate_lock = threading.Lock()
_last_call = [0.0]


def chat(user_prompt, system_prompt=None, temperature=0.2, max_tokens=2000, timeout=30,
         retries=None):
    """单次对话，返回文本；失败（无Key/超时/限流）自动重试；仍失败返回 None。

    注意：命中 HTTP 429（硬限流）时直接降级返回 None，不重试——
    限流通常要等到配额重置点才会恢复，重试只会白白拖慢整个流水线。
    """
    if not TOKENHUB_API_KEY:
        return None
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    payload = {
        "model": HUNYUAN_MODEL,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    last_err = None
    empty_retried = False
    # retries 用于「单次很慢的调用」（如代码生成）：放宽 timeout 后若还失败，
    # 重试 3 次会拖到几分钟，调用方可以把它压到 1–2 次。
    _n = int(retries) if retries else _MAX_RETRY
    for attempt in range(_n):
        # 限速：确保与上次调用间隔 >= _MIN_INTERVAL
        with _rate_lock:
            wait = _MIN_INTERVAL - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
        try:
            req = urllib.request.Request(
                _ENDPOINT,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {TOKENHUB_API_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content")
            if content is None or not content.strip():
                choice = data.get("choices", [{}])[0] or {}
                rdetail = (data.get("usage", {}) or {}).get("completion_tokens_details", {}) or {}
                # hy3 是推理模型：max_tokens 太小时预算全被「思考」烧光，
                # 表现为 content 空 + finish_reason=length + reasoning_tokens>0。
                # 处理：放大 max_tokens 立即重试（最多 _MAX_RETRY 次内）。
                if choice.get("finish_reason") == "length" and rdetail.get("reasoning_tokens"):
                    payload["max_tokens"] = min(int(payload.get("max_tokens") or 800) * 4, 16000)
                    last_err = f"reasoning ate budget (finish=length), retry with max_tokens={payload['max_tokens']}"
                    continue
                # 真·空内容 = 瞬时限流/抖动信号：只重试一次，多次重试无益且拖慢流水线
                last_err = "empty content from TokenHub"
                if not empty_retried:
                    empty_retried = True
                    time.sleep(0.8)
                    continue
                return None
            return content
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # 硬限流：距离重置还有时间，重试无意义，直接降级
                last_err = "HTTP 429 (rate limited)"
                print(f"[hunyuan] 命中限流(429)，跳过重试直接降级")
                return None
            last_err = e
            if attempt < _n - 1:
                time.sleep(0.6 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < _n - 1:
                time.sleep(0.6 * (attempt + 1))
    print(f"[hunyuan] 调用失败（已重试{_n}次）：{last_err}")
    return None


def _strip_md(s):
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    return s.strip()


def chat_json(user_prompt, system_prompt=None, temperature=0.2):
    """调用并尽力解析出 JSON（dict 或 list）。失败返回 None。"""
    text = chat(user_prompt, system_prompt=system_prompt, temperature=temperature)
    if not text:
        return None
    s = _strip_md(text)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}|\[.*\]", s, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
