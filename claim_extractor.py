"""
断言抽取：把一段文字拆成「可被查证的事实性断言」。
提供两种实现：
  1) heuristic_extract —— 纯规则，无需任何外部依赖，离线可用（兜底）。
  2) llm_extract        —— 调用腾讯混元大模型 Hy3 preview（TokenHub 平台，OpenAI 兼容协议），
                          抽取更精准（推荐，赛前必接）。

重要变更（2026-06-22 起）：
  腾讯云旧版混元 ChatCompletions API（hunyuan-turbo / -standard / -pro 等）已全面下线，
  新模型 hy3-preview 迁移至 TokenHub 平台，使用 OpenAI 兼容协议：
      base_url = https://tokenhub.tencentmaas.com/v1
      api_key  = TokenHub 控制台创建的独立 API Key（不是腾讯云 SecretId/SecretKey）
  因此 llm_extract 改为请求 TokenHub；未配置 TOKENHUB_API_KEY 或调用失败时，自动回退到启发式，保证不崩。
"""
import re
import json
import urllib.request
from config import TOKENHUB_API_KEY, HUNYUAN_MODEL
from intent import recognize_intent
from hunyuan import chat, chat_json

# 简单的中文/英文句子切分
_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")

# 噪声过滤：太短、纯客套、无实质信息的句子不要
_STOP_HINTS = ["大家好", "谢谢", "希望", "我觉得", "总之", "综上所述", "需要注意的是"]


def heuristic_extract(text):
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    claims = []
    for s in sentences:
        if len(s) < 8:                      # 太短不是事实断言
            continue
        if any(h in s for h in _STOP_HINTS):  # 主观/客套话跳过
            continue
        # 保留「含数字/日期/专有名词感」或较长的陈述句——更可能是可查证断言
        if re.search(r"\d|年|月|日|％|%|元|万|亿|大学|公司|国家|法|条例", s) or len(s) >= 18:
            claims.append(s)
    # 去重保序
    seen, out = set(), []
    for c in claims:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:12]  # 单次查证控制条数，避免刷爆免费额度


# 抽取提示词：让混元只吐一个 JSON 字符串数组
_EXTRACT_PROMPT = """你是一个事实核查助手。请从下面的文本中抽取出所有「可被客观查证的事实性断言」，用于后续联网检索核对。

要求：
1. 每条断言必须是独立、自包含、可被检索验证的句子或短语（例如数字、日期、比例、排名、因果关系、政策、事件等）。
2. 不要抽取纯主观评价、情绪表达、客套话、号召性语句（如"希望大家""总之""谢谢"）。
3. 若原文一句包含多个事实，请拆分成多条。
4. 保留原文关键信息，不要改写导致语义变化。
5. 只输出 JSON，格式为字符串数组，例如 ["断言1","断言2"]，不要任何额外解释，不要使用 markdown 代码块，使用英文双引号。

待抽取文本：
{text}"""


def _parse_json_list(content):
    """从模型返回里抠出字符串数组，兼容代码块/前后废话。失败返回 None。"""
    if not content:
        return None
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        data = json.loads(s)
    except Exception:
        m = re.search(r"\[.*\]", s, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
    if isinstance(data, list):
        out = [str(x).strip() for x in data if str(x).strip()]
        return out or None
    return None


def llm_extract(text):
    """调用 TokenHub 上的腾讯混元 Hy3 preview 做断言抽取。
    使用 OpenAI 兼容协议，直接 requests 调用，无额外依赖。
    失败/未配置 Key/返回非 JSON 时返回 None（交由调用方回退启发式）。"""
    if not TOKENHUB_API_KEY:
        return None
    try:
        payload = {
            "model": HUNYUAN_MODEL,
            "messages": [{"role": "user", "content": _EXTRACT_PROMPT.format(text=text)}],
            "stream": False,
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            "https://tokenhub.tencentmaas.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {TOKENHUB_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return _parse_json_list(content)
    except Exception as e:  # noqa: BLE001
        print(f"[claim_extractor] TokenHub 混元调用失败，回退启发式：{e}")
        return None


def extract_claims(text, use_llm=False):
    """返回 (claims, method)，method 为 'hunyuan' 或 'heuristic'，供报告卡标注技术栈。"""
    if use_llm:
        claims = llm_extract(text)
        if claims:
            return claims[:12], "hunyuan"
    return heuristic_extract(text), "heuristic"


# 一次性完成「查询拆分 + 意图识别 + 查询改写」的合并提示词
_ANALYZE_PROMPT = """你是一个事实核查预处理模块。给定一段用户输入，请一次性完成三件事，只输出一个 JSON：
{
  "scene": "rumor" 或 "ai" 或 "fact" 或 "opinion",
  "intent_type": "简短意图描述，如：健康谣言甄别 / AI生成内容事实性核对 / 数据类事实核查",
  "confidence": 0到1的小数,
  "claims": [ {"claim":"原断言（独立、自包含、可被检索验证）", "query":"改写后更利于检索的查询词(12-25字)"} ]
}
规则：
- scene：涉及养生/治病/防病毒/偏方/转发提醒/家族群口吻 → rumor；AI生成/AI回答/科技产品参数/AI幻觉风险 → ai；纯客观数据事件 → fact（归入ai）；明显主观无事实 → opinion（归入ai）
- claims：每条必须独立自包含，保留关键数字/日期/比例/专有名词；一句含多事实请拆分；不要抽取纯主观评价/客套话/号召语；最多 8 条
- 【数值断言铁律】原句若同时含「宣称/声称的数值」与「实际基数」，二者必须写在同一条 claim 里，
  禁止把宣称数值删掉。例：「宣称销量同比增长300%，从80万件增至200万件」不可写成
  「去年销量80万件增至今年200万件」（丢了 300% 就无法核验虚假宣传了）；
  正确写法：「某品牌宣称销量同比增长300%（去年80万件、今年200万件）」
- query：补全省略实体、将口语/营销话术转为客观关键词、保留关键数字与专有名词，长度 12-25 字
只输出 JSON，不要任何解释文字。"""

_SCENE_MAP = {"rumor": "rumor", "ai": "ai", "fact": "ai", "opinion": "ai"}


def analyze_claims(text, use_llm=True, fallback_scene="ai"):
    """合并调用：一次混元完成 拆分 + 意图识别 + 查询改写。
    返回 (claims, rewrites, intent, method)。
    - claims: list[str]      待查证断言
    - rewrites: dict[str,str] 断言→改写查询词
    - intent: dict           意图识别结果（含 scene/intent_type/engine 等）
    - method: 'hunyuan' | 'heuristic'
    无 Key / 解析失败 → 回退到规则拆分 + 启发式意图 + 原句改写。"""
    if not (use_llm and TOKENHUB_API_KEY):
        claims = heuristic_extract(text)
        intent = recognize_intent(text, fallback_scene)
        return claims, {c: c for c in claims}, intent, "heuristic"

    out = chat_json(_ANALYZE_PROMPT + "\n\n用户输入：\n" + text, temperature=0.2)
    if not isinstance(out, dict) or not isinstance(out.get("claims"), list):
        # 解析失败：回退拆分，但意图仍用混元单独识别（一次轻量调用）
        claims = heuristic_extract(text)
        intent = recognize_intent(text, fallback_scene)
        return claims, {c: c for c in claims}, intent, "heuristic"

    claims, rewrites = [], {}
    for item in out["claims"][:8]:
        if isinstance(item, dict):
            c = (item.get("claim") or "").strip()
            q = (item.get("query") or "").strip()
        else:
            c = str(item).strip()
            q = ""
        if not c:
            continue
        claims.append(c)
        rewrites[c] = q or c
    if not claims:
        claims = heuristic_extract(text)
        rewrites = {c: c for c in claims}
        method = "heuristic"
    else:
        method = "hunyuan"

    try:
        conf = float(out.get("confidence", 0.6) or 0.6)
    except Exception:
        conf = 0.6
    intent = {
        "scene": _SCENE_MAP.get(out.get("scene"), fallback_scene),
        "intent_type": out.get("intent_type", ""),
        "confidence": conf,
        "explanation": out.get("explanation", ""),
        "engine": "hunyuan",
    }
    return claims, rewrites, intent, method


# —— OCR 截图文字清洗：去界面杂讯，先用混元提炼「核心可查证断言」再进流水线 ——
_CLEAN_OCR_PROMPT = """下面是从一张社交/聊天截图 OCR 识别出的原始文字，混有大量界面杂讯（账号名/频道名、@句柄、时间、点赞转发数、按钮文字、表情符号、乱码等）。

请提炼这张截图真正要传播的「核心可查证断言」：
1. 只保留事实性说法（人物事件、数字、日期、信息来源、因果关系），去掉所有杂讯；
2. 被换行或乱序打断的句子，合并成通顺完整的句子；
3. 保留关键信息（人名、数字、年龄、"某媒体报道"等），不改写语义、不添加猜测；
4. 有多条独立说法用换行分隔；若没有可查证内容，输出信息量最大的一句原文。
5. 只输出提炼后的文字本身，不要任何解释、前缀或引号。

OCR 原始文字：
{raw}"""


def clean_ocr_text(raw):
    """用混元 hy3 从 OCR 原始文字提炼核心断言、剥离界面杂讯。
    返回 (text, method)：method='hunyuan' | 'raw'（未配置Key/限流/结果异常时回退原文，不崩）。"""
    raw = (raw or "").strip()
    if len(raw) < 30:  # 太短没有杂讯可清，省一次调用
        return raw, "raw"
    out = chat(_CLEAN_OCR_PROMPT.format(raw=raw), temperature=0.1, max_tokens=500)
    if not out:
        return raw, "raw"
    cleaned = out.strip()
    cleaned = re.sub(r"^[「『\"']+|[」』\"']+$", "", cleaned).strip()  # 去包裹引号
    if "：" in cleaned[:14] and cleaned[:14].split("：")[0] in ("核心断言", "提炼结果", "核心内容", "输出"):
        cleaned = cleaned.split("：", 1)[1].strip()
    # 兜底：空结果/过短/明显幻觉（比原文长太多）→ 回退原文
    if len(cleaned) < 6 or len(cleaned) > max(len(raw) * 2, 200):
        return raw, "raw"
    return cleaned, "hunyuan"
