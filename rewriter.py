"""
② 查询改写
把每条「待查证断言」改写成更利于检索的 query：补全省略实体、口语转客观关键词、保留关键数字与专有名词。
一次性批量改写所有断言（1 次 LLM 调用），无 Key 时原样返回。
"""
from config import TOKENHUB_API_KEY, LLM_REWRITE
from hunyuan import chat_json

_SYSTEM = """你是一个检索查询改写模块。给定若干「待查证断言」，为每一条生成 1 个更利于搜索引擎 / AI 搜索 API 检索的查询词。
改写原则：
1. 补全被省略的主语/实体（如「该校」→具体校名，若原文已给出）。
2. 将口语/营销话术转为客观关键词（如「减重无敌」→「减重 效果 临床证据」）。
3. 必须保留关键数字、日期、比例、专有名词、机构名。
4. 长度控制在 12-25 字，不要问句。
输出 JSON 数组，顺序与输入断言一一对应，每个元素为字符串查询词。只输出 JSON。"""


def rewrite_queries(claims, scene="ai"):
    """返回 dict: {原断言: 改写后查询词}。无 Key 或失败时返回 {原断言: 原断言}。"""
    if not (LLM_REWRITE and TOKENHUB_API_KEY) or not claims:
        return {c: c for c in claims}
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    prompt = f"场景类型：{scene}\n\n待改写断言：\n{numbered}"
    out = chat_json(_SYSTEM + "\n\n" + prompt, temperature=0.2)
    if isinstance(out, list):
        # 尽力按序对齐（长度不一致时越界用原句兜底）
        return {c: (str(out[i]).strip() if i < len(out) and str(out[i]).strip() else c)
                for i, c in enumerate(claims)}
    return {c: c for c in claims}


# ③ 检索重规划：对首轮检索稀薄的断言，剥掉引导语/号召语，生成更干净的检索 query
# （规则实现，零 LLM 调用、零时延、绝不崩；LLM 不可用时也能正常触发重规划回填）
_HEDGE_PREFIX = [
    "根据最新研究", "据报道", "据", "专家说", "有研究表明", "研究发现", "最新",
    "一项", "据称", "网传", "消息称", "紧急通知", "提醒", "注意", "重磅", "突发",
    "网传", "我听说", "听说了吗", "紧急", "速看", "刚刚",
]
_HEDGE_SUFFIX = [
    "，大家转发提醒家人", "！大家转发提醒家人", "，转发提醒", "，请大家转发",
    "，速转", "，转给家人", "，周知", "，警惕", "！！", "！",
]


def broaden_query(query):
    """去掉引导/号召性前缀后缀，返回更利于检索的干净 query；若无法简化则返回原句。"""
    q = (query or "").strip()
    if not q:
        return q
    for p in _HEDGE_PREFIX:
        if q.startswith(p):
            q = q[len(p):].strip("，,。. ")
    for s in _HEDGE_SUFFIX:
        if q.endswith(s):
            q = q[: -len(s)].strip("，,。. ！!")
    # 去掉首尾引号
    q = q.strip('"').strip("'")
    return q if q and q != query.strip() else query.strip()
