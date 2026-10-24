"""
赛前预热：把 4 个内置示例跑一遍并写入磁盘缓存。

用法（比赛当天提前 20–30 分钟跑一次，可反复跑直到全绿）：
    python warmup.py

跑完之后，页面上点「一键示例」会秒出结果（实测完整流水线 60–180 秒，
预热后 < 1 秒），现场演示不会因为等待而冷场。

缓存存在 .cache/ 目录，服务重启后依然有效。
换了示例文本或想强制重跑：python warmup.py --force
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 关闭混元调用限速带来的额外等待：预热是一次性批量任务，限速锁只保护并发场景
os.environ.setdefault("WARMUP", "1")

from app import DEMO_TEXTS  # noqa: E402
from verifier import verify_text, fuzzy_find  # noqa: E402
import cache as report_cache  # noqa: E402

FORCE = "--force" in sys.argv


def main():
    print("=" * 62)
    print("AI 侦探社 · 赛前预热")
    print("=" * 62)
    if FORCE:
        report_cache.clear()
        print("(已清空旧缓存，强制重跑)\n")

    ok = 0
    total = len(DEMO_TEXTS)
    for scene, text in DEMO_TEXTS.items():
        if not FORCE and report_cache.get(scene, text):
            print(f"[跳过] {scene:<7} 已有缓存")
            ok += 1
            continue
        t0 = time.time()
        try:
            out = fuzzy_find(text) if scene == "fuzzy" else verify_text(text, scene=scene)
        except Exception as e:  # noqa: BLE001
            print(f"[失败] {scene:<7} {e}")
            continue
        dt = time.time() - t0
        report_cache.put(scene, text, out)
        # verify_text 返回 items；fuzzy_find 返回 candidates
        rows = out.get("items") or out.get("candidates") or []
        print(f"[完成] {scene:<7} {dt:6.1f}s  条目 {len(rows)}  结论："
              f"{str(out.get('headline') or out.get('verdict') or '')[:34]}")
        ok += 1

    print("\n" + "-" * 62)
    st = report_cache.stats()
    print(f"缓存状态：磁盘 {st['disk']} 条 / 内存 {st['memory']} 条")
    if ok == total:
        print("✅ 全部就绪 —— 现场点「一键示例」应秒出结果。")
        print("   建议再手动打开页面点一次每个 tab 的一键示例，确认视觉无误。")
    else:
        print(f"⚠️ 仅 {ok}/{total} 个场景就绪，失败的可以重跑本脚本（已完成的会跳过）。")
    print("-" * 62)


if __name__ == "__main__":
    main()
