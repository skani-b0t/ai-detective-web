"""
核对引擎（多层过滤 + 跨源交叉验证 + 代码核验 + 内容融合）
这是整个工具的大脑——也是比赛里最容易做出差异化的地方。

整条流水线（与「可信源矩阵 + 多层过滤 + 交叉验证 + 代码核验」架构对齐）：
  ① 意图识别与对齐   intent.py
  ② 查询改写         rewriter.py
  ③ 查询拆分         claim_extractor.py（混元/规则）
  ④ 多路信息源检索   wsa_client.search_multi（元宝搜索 多 Mode/Industry 并行）
  ⑤ 检索重规划       首轮稀薄则剥离引导语回填一次
  ⑥ 多层过滤         L1 冲突词 / L2 重叠度 / L4 权威加权
  ⑦ 代码核验（数值型） code_verify.py（混元生成纯计算代码 + 沙箱执行）
  ⑧ 跨源交叉验证     L3 独立域一致性 + L5 混元语义核验
  ⑨ 内容融合与生成   generate_narrative（混元总评）

判定四态：
  ✅ 有据可查   多层证据一致支撑
  ⚠️ 存疑       有结果但支撑力度一般 / 证据相互矛盾
  ❌ 与信源冲突  权威信源明确否定 / 混元语义判为反驳 / 代码核验明确不符
  🔍 查无来源   几乎没有可用信源支撑
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from config import (WSA_MODE, LLM_SEMANTIC, SEMANTIC_MAX, LLM_FUSION, TOKENHUB_API_KEY,
                    CODE_VERIFY, CODE_MAX, CODE_BUDGET, REPLAN_MIN)
from claim_extractor import analyze_claims
from wsa_client import search_multi
from source_matrix import authority_weight
from rewriter import broaden_query
from code_verify import verify_numeric, is_numeric_claim, is_computable_claim
from hunyuan import chat, chat_json

_CONFLICT_MARKERS = ["不实", "辟谣", "系谣言", "谣言", "虚假", "造谣", "并未",
                     "不存在", "否认", "已辟谣", "fake", "false", "debunk", "查无此事", "误区", "骗局"]
_PUNCT = re.compile(r"[^\w一-鿿]")


def _bigrams(text):
    t = _PUNCT.sub("", text)
    if len(t) < 2:
        return set(t)
    return set(t[i:i + 2] for i in range(len(t) - 1))


def _overlap_ratio(claim, snippet):
    grams = _bigrams(claim)
    if not grams:
        return 0.0
    hit = sum(1 for g in grams if g in snippet)
    return hit / len(grams)


def _domain(url):
    m = re.search(r"https?://([^/]+)/?", url or "")
    return m.group(1) if m else "未知来源"


def _merge_results(a, b):
    """按 url 去重合并两次检索结果（保留较高 score），上限 15 条。"""
    seen = {}
    for r in list(a) + list(b):
        u = r.get("url", "")
        if u and u in seen:
            if r.get("score", 0) > seen[u].get("score", 0):
                seen[u] = r
            continue
        r.setdefault("paths", [r.get("path")])
        seen[u] = r
    out = list(seen.values())
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out[:15]


# ——— ⑧ 混元语义核验（L5）———
_SEM_SYS = """你是一个事实核查的「语义核验」模块。给定一条待查证断言和若干检索到的信源摘要，
请判断这些信源整体是「支持 / 反驳 / 无关」该断言，并给出置信度。只输出 JSON：
{"verdict":"support|refute|neutral","confidence":0到1的小数,"reason":"一句话依据"}"""


def _semantic_verify(claim, results):
    """返回 dict: verdict/confidence/reason/engine；失败返回 None。"""
    if not (LLM_SEMANTIC and TOKENHUB_API_KEY):
        return None
    top = results[:5]
    if not top:
        return None
    ctx = "\n".join(f"- {r.get('title','')}：{r.get('summary','')[:120]}" for r in top)
    prompt = f"待查证断言：{claim}\n\n检索到的信源摘要：\n{ctx}"
    out = chat_json(_SEM_SYS + "\n\n" + prompt, temperature=0.1)
    if not isinstance(out, dict):
        return None
    try:
        conf = float(out.get("confidence", 0.5) or 0.5)
    except Exception:
        conf = 0.5
    return {
        "verdict": out.get("verdict", "neutral"),
        "confidence": conf,
        "reason": out.get("reason", ""),
        "engine": "hunyuan",
    }


def verify_claim(claim, query, results, idx=0, use_llm=True, semantic=None,
                 skip_semantic=False, code_result=None):
    """对单条断言做多层过滤 + 跨源交叉验证（+代码核验影响），产出四态判定与溯源 layers。"""
    if not results:
        layers = {"conflict": False, "overlap": 0.0, "authority_hits": 0,
                  "independent_domains": 0, "semantic": None}
        item = {
            "claim": claim, "query": query, "verdict": "🔍", "label": "查无来源",
            "support": 0, "sources": [], "note": "未检索到可支撑该说法的信源。",
            "layers": layers, "semantic": None,
        }
        if code_result:
            _apply_code(item, code_result)
            item["code"] = code_result
        return item

    # L1 冲突词检测
    conflict = False
    conflict_src = ""
    for r in results:
        blob = (r.get("summary", "") + r.get("title", ""))
        if r.get("score", 0) >= 0.6 and any(m in blob for m in _CONFLICT_MARKERS):
            conflict = True
            conflict_src = r.get("title", "")
            break

    # L2 重叠度（前3条）
    top = results[:3]
    overlaps = [_overlap_ratio(claim, r.get("summary", "") + r.get("title", "")) for r in top]
    overlap = round(sum(overlaps) / len(overlaps), 3)

    # L3/L4 跨源一致性 + 权威加权
    domains = set()
    authority_hits = 0
    wsum = 0.0
    for r in results:
        domains.add(_domain(r.get("url", "")))
        tier = (r.get("authority") or {}).get("tier", 1)
        wsum += authority_weight(tier)
        if tier >= 2:
            authority_hits += 1
    authority_ratio = round(wsum / len(results), 3) if results else 0.0
    independent_domains = len(domains)

    # L5 混元语义核验
    if semantic is None and not skip_semantic and use_llm and idx < SEMANTIC_MAX:
        semantic = _semantic_verify(claim, results)

    # 融合支持度
    if semantic:
        support = round(0.35 * overlap + 0.35 * authority_ratio + 0.30 * semantic["confidence"], 3)
    else:
        support = round(0.5 * overlap + 0.5 * authority_ratio, 3)

    # 四态判定
    refute = semantic and semantic["verdict"] == "refute" and semantic["confidence"] >= 0.6
    support_ok = semantic and semantic["verdict"] == "support" and semantic["confidence"] >= 0.6
    if conflict and (authority_hits > 0 or refute):
        verdict, label = "❌", "与信源冲突"
        note = f"检索到权威信源明确否定/辟谣该说法" + (f"（如：{conflict_src}）" if conflict_src else "") + "。"
    elif refute:
        verdict, label = "❌", "与信源冲突"
        note = f"语义核验判定为反驳：{semantic['reason']}"
    elif support_ok or support >= 0.5:
        verdict, label = "✅", "有据可查"
        note = "多层证据一致支撑，结论可追溯。"
    elif support >= 0.25 or (semantic and semantic["verdict"] == "support"):
        verdict, label = "⚠️", "存疑"
        note = "有相关结果但支撑力度一般，或证据相互矛盾，建议进一步核实。"
    elif independent_domains == 0 or support < 0.15:
        verdict, label = "🔍", "查无来源"
        note = "相关信源不足，暂无法支撑该说法。"
    else:
        verdict, label = "⚠️", "存疑"
        note = "证据偏向存疑，建议补充权威信源核实。"

    # ——— ⑦ 代码核验（数值型）影响 ———
    if code_result:
        verdict, label, note = _code_influence(verdict, label, note, code_result, support)
        support = verdict_support_apply(support, code_result)

    sources = [{
        "title": r.get("title", ""), "url": r.get("url", ""),
        "score": r.get("score", 0), "publish_time": r.get("publish_time", ""),
        "source": r.get("source", _domain(r.get("url", ""))),
        "authority_level": r.get("authority_level", 0),
        "authority": r.get("authority", {"tier": 1, "label": "一般信源"}),
    } for r in results[:5]]

    layers = {
        "conflict": conflict, "overlap": overlap,
        "authority_hits": authority_hits, "independent_domains": independent_domains,
        "authority_ratio": authority_ratio, "semantic": (semantic or {}).get("verdict"),
    }

    return {
        "claim": claim, "query": query, "verdict": verdict, "label": label,
        "support": support, "sources": sources, "note": note,
        "layers": layers, "semantic": semantic,
        "code": code_result,
    }


def _set_vl(v, l, n):
    return v, l, n


def _apply_code(item, code_result, verdict=None, label=None, note=None, apply_to=None):
    """兼容两种调用：①传 item（无结果分支）②传 verdict/label/note 字符串（主分支另用 _code_influence）。"""
    if item is not None:
        ok = code_result.get("ok")
        claimed = code_result.get("claimed")
        computed = code_result.get("computed")
        if ok is True:
            item["note"] += f" 🧮代码核验吻合（声明 {claimed}，实测 {computed}）。"
        elif ok is False:
            item["note"] += f" 🧮代码核验不符（声明 {claimed}，实测 {computed}）。"


def _code_influence(verdict, label, note, code_result, support):
    """根据代码核验结果调整四态判定与说明文字。"""
    ok = code_result.get("ok")
    claimed = code_result.get("claimed")
    computed = code_result.get("computed")
    if ok is True:
        note += f" 🧮代码核验吻合（声明 {claimed}，实测 {computed}）。"
        support = min(1.0, support + 0.05)
    elif ok is False:
        note += f" 🧮代码核验不符（声明 {claimed}，实测 {computed}）。"
        if verdict == "✅":
            verdict, label = "⚠️", "存疑"
        elif verdict == "⚠️":
            verdict, label = "❌", "与信源冲突"
    return verdict, label, note


def verdict_support_apply(support, code_result):
    if code_result.get("ok") is True:
        return round(min(1.0, support + 0.05), 3)
    return support


def _generate_narrative(summary_text):
    if not (LLM_FUSION and TOKENHUB_API_KEY):
        return None
    prompt = ("根据以下查证摘要，写一段 60-100 字的中文总评，语气客观专业，"
              "点明整体可信度与主要风险点，不要使用 markdown。\n\n" + summary_text)
    return chat(prompt, temperature=0.4, max_tokens=220)


def verify_text(text, scene="ai", use_llm=True):
    """端到端：一段文字 → 一条完整查证报告（含流水线追踪）。"""
    # ①②③ 合并：查询拆分 + 意图识别与对齐 + 查询改写（一次混元调用）
    claims, rewrites, intent, extract_method = analyze_claims(
        text, use_llm=use_llm, fallback_scene=scene)
    if not claims:
        claims = [c.strip() for c in text.split("\n") if len(c.strip()) >= 8][:8] or [text]
        rewrites = {c: c for c in claims}
        extract_method = "heuristic"
    eff_scene = intent["scene"]
    queries = [rewrites.get(c, c) for c in claims]

    # ④ 多路信息源检索
    def _search_one(args):
        c, q = args
        return search_multi(q, scene=eff_scene, intent=intent)

    with ThreadPoolExecutor(max_workers=min(len(claims), 6) or 1) as ex:
        all_results = list(ex.map(_search_one, list(zip(claims, queries))))

    # ⑤ 检索重规划：首轮结果稀薄的断言，剥离引导语/号召语回填一次
    replan_log = {}
    for i, (c, q) in enumerate(zip(claims, queries)):
        if len(all_results[i]) < REPLAN_MIN:
            bq = broaden_query(q)
            if bq and bq != q:
                extra = search_multi(bq, scene=eff_scene, intent=intent)
                before = len(all_results[i])
                merged = _merge_results(all_results[i], extra)
                all_results[i] = merged
                if len(merged) > before:
                    replan_log[i] = {"from": q, "to": bq, "before": before, "after": len(merged)}

    # ⑦ 代码核验（数值型）：对数值断言用 Python 沙箱核算
    #    限前 CODE_MAX 条 + 阶段总时间预算 CODE_BUDGET（秒），
    #    避免 hy3 生成代码偶发超时把整条流水线拖慢（实测无预算时单条可空耗 50s）。
    code_results = [None] * len(claims)
    if use_llm and TOKENHUB_API_KEY and CODE_VERIFY:
        n = 0
        _t_code = time.time()
        # 可计算的断言优先：纯事实型数值（分数线/气温/价格）算不出东西，
        # 让它占用宝贵的预算会挤掉真正能抓出矛盾的那条。
        _order = sorted(range(len(claims)),
                        key=lambda i: (not is_computable_claim(claims[i]), i))
        for i in _order:
            if n >= CODE_MAX:
                break
            if time.time() - _t_code > CODE_BUDGET:
                print(f"[verify] 代码核验超出时间预算 {CODE_BUDGET}s，放弃剩余断言")
                break
            # 只对「可计算」的数值断言进沙箱。
            # 原因：纯事实型数值（如「2026年演唱会已获批」含年份即命中数值判断）
            # 沙箱算不出「是否获批」，会给出无意义的 ok=False 并错误降级判定，
            # 实测曾把「查最新」示例误判成「检出冲突」。宁可漏，不可误伤。
            if is_computable_claim(claims[i]):
                cr = verify_numeric(claims[i])
                if cr:
                    code_results[i] = cr
                    n += 1

    # ⑥ 多层过滤 + ⑧ 跨源交叉验证（先纯规则层，零 LLM 调用，快速出判定）
    def _verify_rule(args):
        c, q, rs, i = args
        return verify_claim(c, q, rs, idx=i, use_llm=use_llm,
                            skip_semantic=True, code_result=code_results[i])

    with ThreadPoolExecutor(max_workers=min(len(claims), 6) or 1) as ex:
        items = list(ex.map(_verify_rule,
                            [(c, q, rs, i) for i, (c, q, rs) in enumerate(zip(claims, queries, all_results))]))
    for i, it in enumerate(items):
        if i in replan_log:
            it["replan"] = replan_log[i]
        if code_results[i]:
            it["code"] = code_results[i]

    # ⑧ 跨源交叉验证 · L5 混元语义核验：仅对第一条「存疑」断言精验（最多 1 次 LLM 调用，省额度）
    if use_llm and TOKENHUB_API_KEY:
        target = next((i for i, it in enumerate(items) if it["verdict"] == "⚠️"), None)
        if target is not None:
            sem = _semantic_verify(claims[target], all_results[target])
            if sem:
                items[target] = verify_claim(
                    claims[target], queries[target], all_results[target],
                    idx=target, use_llm=use_llm, semantic=sem, code_result=code_results[target])
                if target in replan_log:
                    items[target]["replan"] = replan_log[target]
                items[target]["code"] = code_results[target]
    semantic_engine = "hunyuan" if any((it.get("semantic") or {}).get("verdict") for it in items) else "规则"

    # 报告汇总
    supports = [it["support"] for it in items]
    consistency = round(sum(supports) / len(supports) * 100) if supports else 0
    verified = sum(1 for ci in items if ci["verdict"] == "✅")
    conflict = sum(1 for ci in items if ci["verdict"] == "❌")
    uncertain = sum(1 for ci in items if ci["verdict"] == "⚠️")
    noev = sum(1 for ci in items if ci["verdict"] == "🔍")

    # 独立信源（跨全部断言去重域名）
    domains = set()
    for it in items:
        for s in it["sources"]:
            domains.add(_domain(s["url"]))
    independent = len(domains)

    # 可信源矩阵汇总
    matrix = {3: 0, 2: 0, 1: 0, 0: 0}
    for it in items:
        for s in it["sources"]:
            t = (s.get("authority") or {}).get("tier", 1)
            matrix[t] = matrix.get(t, 0) + 1

    # 整体结论文案
    if conflict:
        headline = "⚠️ 检出与权威信源/代码核验冲突的内容，请谨慎采信。"
    elif consistency >= 70 and verified >= max(1, len(items) // 2):
        headline = "✅ 整体结论有据可查，来源可追溯。"
    elif consistency >= 40:
        headline = "⚠️ 部分结论存疑，建议补充核实。"
    else:
        headline = "🔍 多数结论缺乏可支撑信源。"

    # ⑨ 内容融合与生成（混元总评）
    summary_blob = headline + "\n" + "\n".join(
        f"[{it['verdict']}{it['label']}] {it['claim']}" for it in items)
    narrative = _generate_narrative(summary_blob)

    # 流水线追踪
    raw_total = sum(len(r) for r in all_results)
    replan_count = sum(1 for r in replan_log.values())
    code_count = sum(1 for c in code_results if c)
    semantic_engine = "hunyuan" if (use_llm and TOKENHUB_API_KEY and LLM_SEMANTIC) else "规则"
    pipeline = [
        {"stage": "① 意图识别与对齐", "detail": intent["intent_type"] or "—",
         "engine": intent["engine"]},
        {"stage": "② 查询改写", "detail": f"{len(queries)} 条查询已优化为检索词",
         "engine": ("hunyuan" if (use_llm and TOKENHUB_API_KEY) else "原句(无Key)")},
        {"stage": "③ 查询拆分", "detail": f"拆出 {len(claims)} 条可查证断言",
         "engine": extract_method},
        {"stage": "④ 多路信息源检索", "detail": f"元宝搜索多路并行 → {raw_total} 条原始结果",
         "engine": "live" if WSA_MODE == "live" else "offline"},
        {"stage": "⑤ 检索重规划", "detail": f"{replan_count} 条首轮稀薄已回填检索" if replan_count
         else "首轮结果充足，无需重规划", "engine": "规则"},
        {"stage": "⑥ 多层过滤", "detail": "冲突词 / 重叠度 / 权威加权",
         "engine": "规则"},
        {"stage": "⑦ 代码核验（数值型）", "detail": f"{code_count} 条数值断言已用代码核算" if code_count
         else "无数值型断言需核算", "engine": "hunyuan" if code_count else "跳过"},
        {"stage": "⑧ 跨源交叉验证", "detail": f"{independent} 个独立域名交叉印证",
         "engine": semantic_engine},
        {"stage": "⑨ 内容融合与生成", "detail": "生成总评" if narrative else "总评(无Key跳过)",
         "engine": "hunyuan" if narrative else "跳过"},
    ]

    return {
        "scene": eff_scene,
        "headline": headline,
        "consistency": consistency,
        "stats": {"verified": verified, "conflict": conflict,
                  "uncertain": uncertain, "noev": noev, "total": len(items)},
        "extract_method": extract_method,
        "engine_mode": "live" if WSA_MODE == "live" else "offline",
        "intent": intent,
        "independent_sources": independent,
        "authority_matrix": matrix,
        "narrative": narrative,
        "pipeline": pipeline,
        "items": items,
    }


# ——— 查模糊（线索检索模式）：零碎线索 → 提炼检索词 → 多路检索 → 候选答案 + 可点来源 ———
_FUZZY_SYS = """你是一个「线索检索」模块。用户只记得零碎线索（模糊描述、记不起的名字、半句歌词），
请提炼出最多 3 条可直接用于搜索引擎的查询词：去掉口语填充词，保留区分度最强的关键词。
只输出 JSON：{"queries":["查询词1","查询词2","查询词3"]}"""


def fuzzy_find(text, use_llm=True):
    """「查模糊」模式：不判定真假，只负责把模糊线索检索成带出处的候选答案。"""
    queries = []
    if use_llm and TOKENHUB_API_KEY:
        out = chat_json(_FUZZY_SYS + "\n\n用户线索：" + text, temperature=0.3)
        if isinstance(out, dict) and isinstance(out.get("queries"), list):
            queries = [str(q).strip() for q in out["queries"] if str(q).strip()][:3]
    if not queries:
        b = broaden_query(text.strip())
        queries = [text.strip()] + ([b] if b and b != text.strip() else [])

    raw = []
    with ThreadPoolExecutor(max_workers=min(len(queries), 3) or 1) as ex:
        for res in ex.map(lambda q: search_multi(q, scene="fuzzy"), queries):
            raw.extend(res)

    # url 去重 + 相关度排序
    seen = {}
    for r in raw:
        u = r.get("url", "")
        if not u:
            continue
        if u not in seen or r.get("score", 0) > seen[u].get("score", 0):
            seen[u] = r
    cands = sorted(seen.values(), key=lambda x: x.get("score", 0), reverse=True)[:10]
    candidates = [{
        "title": r.get("title", ""), "url": r.get("url", ""),
        "summary": (r.get("summary", "") or "")[:180],
        "source": r.get("source") or _domain(r.get("url", "")),
        "score": r.get("score", 0),
        "publish_time": r.get("publish_time", ""),
        "authority": r.get("authority", {"tier": 1, "label": "一般信源"}),
    } for r in cands]

    raw_total = len(raw)
    pipeline = [
        {"stage": "① 线索提取与改写", "detail": f"{len(queries)} 条检索词：" + " / ".join(queries[:3]),
         "engine": ("hunyuan" if (use_llm and TOKENHUB_API_KEY) else "原句(无Key)")},
        {"stage": "② 多路信息源检索", "detail": f"元宝搜索多路并行 → {raw_total} 条原始结果",
         "engine": "live" if WSA_MODE == "live" else "offline"},
        {"stage": "③ 去重与排序", "detail": f"{len(seen)} 个独立结果，按相关度取前 {len(candidates)} 条",
         "engine": "规则"},
        {"stage": "④ 候选呈现", "detail": "只摆证据不下结论，来源均点得开" if candidates
         else "没查到可靠来源——明说，不编", "engine": "规则"},
    ]

    if candidates:
        headline = f"从「{text.strip()[:20]}…」检索到 {len(candidates)} 条候选，来源均点得开。"
    else:
        headline = "没查到可靠来源。这条线索我先认怂——不猜、不编，换个说法再试一次？"

    return {
        "mode": "fuzzy",
        "headline": headline,
        "queries": queries,
        "candidates": candidates,
        "engine_mode": "live" if WSA_MODE == "live" else "offline",
        "pipeline": pipeline,
    }
