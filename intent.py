"""
① 意图识别与对齐
判断用户输入属于哪种查证场景 / 意图类型，并把下游「场景(ai/rumor)」对齐好。
无 TokenHub Key 时回退到基于关键词的启发式判断。
"""
from config import TOKENHUB_API_KEY, LLM_INTENT
from hunyuan import chat_json

_SYSTEM = """你是一个事实核查系统的「意图识别」模块。给定一段用户输入（可能是 AI 回答、网文、家族群消息、社媒帖子等），
请判断它的查证意图，只输出 JSON：
{
  "scene": "rumor" 或 "ai" 或 "fact" 或 "opinion",
  "intent_type": "简短意图描述，如：健康谣言甄别 / AI生成内容事实性核对 / 数据类事实核查 / 观点类陈述 / 产品营销夸大",
  "confidence": 0到1的小数,
  "explanation": "一句话说明判断依据"
}
判断规则：
- 涉及养生/治病/防病毒/偏方/转发提醒/家族群口吻/「紧急通知」「大家转发」→ rumor
- 涉及 AI 生成、AI 回答、模型输出、科技产品参数、明显 AI 幻觉风险 → ai
- 纯客观数据/事件陈述（含数字、日期、排名、比例）→ fact（归入 ai 场景做事实核查）
- 明显主观评价、无事实可查 → opinion（归入 ai 场景，仅做提示）
只输出 JSON，不要任何解释文字。"""

_SCENE_MAP = {"rumor": "rumor", "ai": "ai", "fact": "ai", "opinion": "ai"}


def _heuristic(text, fallback):
    t = text.lower()
    rumor_hints = ["谣言", "偏方", "养生", "预防", "转发", "紧急通知", "病毒", "致癌", "秘方",
                   "幽门", "大蒜", "白酒", "量子", "治愈", "保健品", "家人"]
    if any(h in t for h in rumor_hints):
        return {"scene": "rumor", "intent_type": "疑似健康/养生类谣言甄别", "confidence": 0.6,
                "explanation": "命中养生/谣言类关键词（启发式）", "engine": "heuristic"}
    return {"scene": fallback, "intent_type": "事实性内容核查", "confidence": 0.5,
            "explanation": "未命中谣言特征，默认事实核查（启发式）", "engine": "heuristic"}


def recognize_intent(text, fallback_scene="ai"):
    """返回 dict: scene / intent_type / confidence / explanation / engine。"""
    if not (LLM_INTENT and TOKENHUB_API_KEY):
        return _heuristic(text, fallback_scene)
    out = chat_json(_SYSTEM + "\n\n用户输入：\n" + text, temperature=0.1)
    if not isinstance(out, dict):
        return _heuristic(text, fallback_scene)
    scene = _SCENE_MAP.get(out.get("scene"), fallback_scene)
    try:
        conf = float(out.get("confidence", 0.5) or 0.5)
    except Exception:
        conf = 0.5
    return {
        "scene": scene,
        "intent_type": out.get("intent_type", ""),
        "confidence": conf,
        "explanation": out.get("explanation", ""),
        "engine": "hunyuan",
    }
