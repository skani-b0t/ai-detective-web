"""性能打点：定位九阶段流水线各环节耗时（赛前调优用，不参与比赛演示）。"""
import sys
import time
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from claim_extractor import analyze_claims, clean_ocr_text
from wsa_client import search_multi
from verifier import verify_claim, _semantic_verify, _generate_narrative
from code_verify import verify_numeric, is_numeric_claim
from hunyuan import chat, chat_json
from config import TOKENHUB_API_KEY

T0 = [time.time()]


def mark(tag):
    now = time.time()
    print(f"[{now - T0[0]:7.2f}s] (+{now - T0[0] - (mark.prev if hasattr(mark, 'prev') else 0):6.2f}s) {tag}")
    mark.prev = now - T0[0]


mark.prev = 0.0

TEXT = ("根据最新研究，每天喝白酒可以杀死幽门螺杆菌，预防胃癌。"
        "XX大学2026年录取分数线为680分，排名全国前五。")

# 0. 单次 hy3 裸调用耗时
mark("--- 基准：单次 hy3 调用 ---")
r = chat("只回复两个字：你好", max_tokens=2000)
mark(f"hy3 单次返回：{str(r)[:30]!r}")

# 1. 单次元宝搜索
mark("--- 基准：单次元宝搜索 search_multi ---")
res = search_multi("喝白酒能杀死幽门螺杆菌吗", scene="rumor")
mark(f"search_multi 返回 {len(res)} 条")

# 2. ①②③ 拆分+改写+意图
mark("--- ①②③ analyze_claims（1 次 hy3）---")
claims, rewrites, intent, method = analyze_claims(TEXT, use_llm=True, fallback_scene="ai")
mark(f"拆出 {len(claims)} 条断言，方法={method}")

# 3. ④ 多路检索（并行）
mark("--- ④ 多路检索 ---")
from concurrent.futures import ThreadPoolExecutor
qs = [rewrites.get(c, c) for c in claims]
with ThreadPoolExecutor(max_workers=min(len(claims), 6)) as ex:
    allres = list(ex.map(lambda q: search_multi(q, scene=intent["scene"], intent=intent), qs))
mark(f"检索完成，总条数 {sum(len(x) for x in allres)}")

# 4. ⑦ 代码核验
mark("--- ⑦ 代码核验（数值断言）---")
for c in claims:
    if is_numeric_claim(c):
        cr = verify_numeric(c)
        mark(f"  数值断言「{c[:20]}」核算结果={bool(cr)}")
        break

# 5. ⑥ 规则层过滤
mark("--- ⑥ 多层过滤（纯规则）---")
items = [verify_claim(c, q, rs, skip_semantic=True)
         for c, q, rs in zip(claims, qs, allres)]
mark("规则层判定完成")

# 6. ⑧ L5 语义核验
mark("--- ⑧ L5 混元语义核验（1 次 hy3）---")
tgt = next((i for i, it in enumerate(items) if it["verdict"] == "⚠️"), None)
if tgt is not None:
    _semantic_verify(claims[tgt], allres[tgt])
    mark("语义核验完成")
else:
    mark("无存疑断言，跳过")

# 7. ⑨ 总评
mark("--- ⑨ 内容融合总评（1 次 hy3）---")
_generate_narrative("测试")
mark("总评完成")

print(f"\n===== 全流程合计 {time.time() - T0[0]:.2f}s =====")
