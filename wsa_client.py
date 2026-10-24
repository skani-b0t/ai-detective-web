"""
元宝搜索 API 客户端（wsa / SearchPro）
- offline 模式：返回 sample_data 预置结果，无需 Key、无需联网。
- live  模式：调用腾讯云 wsa.SearchPro 接口（腾讯云 SDK 懒加载，未安装也不影响 offline）。

wsa 接口要点（来自官方文档）：
  域名 wsa.tencentcloudapi.com，Action=SearchPro，Version=2025-05-08，默认限频 20次/秒
  关键参数：Query(必填) / Mode(0自然,1垂直权威VR,2混合) / Cnt(10~50) /
           Site(指定/排除站点) / Industry(gov/news/acad/finance) / Freshness(dN/mN/yN)
  返回：JSON，含 标题 / 摘要 / 来源URL / 相关性得分 / 发布时间 等
"""
import json
from concurrent.futures import ThreadPoolExecutor
from config import WSA_MODE, WSA_SECRET_ID, WSA_SECRET_KEY, WSA_MAX_RESULTS, WSA_MULTIPATH
from sample_data import mock_search
from source_matrix import classify_domain

# 假数据降级告警：只打一次，避免刷屏。
# 背景：曾经因为 tencentcloud SDK 未安装，_search_live 抛 ImportError 被静默兜底成 mock，
# 导致整个演示跑在硬编码样例上（来源标题带"样例"、URL 是占位域名）却没人发现。
_MOCK_WARNED = [False]


def _warn_mock(reason):
    if _MOCK_WARNED[0]:
        return
    _MOCK_WARNED[0] = True
    print("\n" + "!" * 66)
    print(f"!!! 警告：元宝搜索正在返回【预置假数据】，真实检索未生效！")
    print(f"!!! 原因：{reason}")
    print("!!! 后果：来源标题含「样例」、URL 为占位域名，评委一眼可辨。")
    print("!!! 处理：pip install tencentcloud-sdk-python，并确认 .env 里 WSA_MODE=live 且密钥已填。")
    print("!" * 66 + "\n")


def search(query, scene="ai", mode=2, cnt=None, freshness=None, site=None, industry=None):
    """
    统一搜索入口。
    :param query: 搜索词（通常是被核对的一条断言）
    :param scene: "ai" 或 "rumor"，用于选默认参数与样例数据
    :return: list[dict] 每项含 title/url/summary/score/publish_time/source
    """
    cnt = cnt or WSA_MAX_RESULTS

    if WSA_MODE == "offline":
        _warn_mock("WSA_MODE=offline（人工指定）")
        return mock_search(query, scene)
    if not (WSA_SECRET_ID and WSA_SECRET_KEY):
        raise RuntimeError("真实搜索未启用：请配置 WSA_SECRET_ID 和 WSA_SECRET_KEY")

    return _search_live(query, mode=mode, cnt=cnt, freshness=freshness, site=site, industry=industry)


def _pick(obj, *names, default=""):
    """兼容 SDK 对象属性(Title)与原始 dict(title)两种返回形态。"""
    for n in names:
        if isinstance(obj, dict):
            v = obj.get(n)
        else:
            v = getattr(obj, n, None)
        if v not in (None, ""):
            return v
    return default


def _normalize_pages(pages):
    """SDK 返回 Response.Pages 是 `list[str]`，每个元素是若干页详情的 JSON 字符串。
    这里把它安全地解析为 list[dict]；同时兼容已经是 list[dict] 的情况。"""
    out = []
    for raw in pages or []:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                continue
        if isinstance(raw, dict):
            out.append(raw)
    return out


def _search_live(query, mode=2, cnt=10, freshness=None, site=None, industry=None):
    """真实调用腾讯云 wsa.SearchPro。SDK 懒加载，避免离线环境因缺包报错。

    真实返回结构（已用真实响应校准）：
      Response.Pages = [ { title, url, date, passage, content,
                          site, score, authority_level, favicon, pics }, ... ]
    """
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
        from tencentcloud.wsa.v20250508 import wsa_client, models
    except ImportError as e:
        raise RuntimeError("真实搜索未启用：请安装 tencentcloud-sdk-python") from e

    try:
        cred = credential.Credential(WSA_SECRET_ID, WSA_SECRET_KEY)
        client = wsa_client.WsaClient(cred, "")
        req = models.SearchProRequest()
        req.Query = query
        req.Mode = mode
        req.Cnt = cnt
        if freshness:
            req.Freshness = freshness
        if site:
            req.Site = site
        if industry:
            req.Industry = industry
        resp = client.SearchPro(req)

        # 兼容 SDK 直接返回对象 与 Response 包裹 两种形态
        pages = _pick(resp, "Pages")
        if not pages and hasattr(resp, "Response"):
            pages = _pick(resp.Response, "Pages")
        if not pages:
            pages = _pick(resp, "Results")  # 老文档写法兜底
        pages = _normalize_pages(pages)

        results = []
        for item in pages:
            results.append({
                "title": _pick(item, "Title", "title", default=""),
                "url": _pick(item, "Url", "url", default=""),
                "summary": _pick(item, "Passage", "passage", "Abstract",
                                "Summary", "Content", "content", default=""),
                "score": float(_pick(item, "Score", "score", default=0) or 0),
                "publish_time": str(_pick(item, "Date", "date",
                                        "PublishTime", "publish_time", default="") or ""),
                "source": _pick(item, "Site", "site", "Source", "source", default=""),
                # 真实接口独有：信源权威度分级（0~4+），直接进报告卡当可信度标签
                "authority_level": int(_pick(item, "AuthorityLevel",
                                            "authority_level", default=0) or 0),
                "favicon": _pick(item, "Favicon", "favicon", default=""),
            })
        return results
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"元宝搜索调用失败：{e}") from e


def _tag_authority(results):
    """给每条结果附加可信源矩阵分级信息（tier/label/whitelist…）。"""
    for r in results:
        cls = classify_domain(r.get("url", ""), r.get("authority_level", 0))
        r["authority"] = cls
    return results


# 多路检索的「路」配置：(tag, Mode, Industry, Freshness)
# Mode: 0 自然结果 / 1 垂直权威(VR) / 2 混合
_PATHS_RUMOR = [
    ("news_y", 2, "news", "y1"),
    ("vr", 1, None, "y1"),
]
_PATHS_AI = [
    ("mixed", 2, None, "y1"),
    ("vr", 1, None, None),
    ("acad", 2, "acad", None),
]
# 「查最新」：两条路都带强时效约束（一个月内新闻 + 一周内自然结果），突出分钟级更新卖点
_PATHS_LATEST = [
    ("news_m1", 2, "news", "m1"),
    ("fresh_w1", 2, None, "w1"),
    ("vr", 1, None, None),
]


def search_multi(query, scene="ai", intent=None):
    """④ 多路信息源检索：按场景并行发起多组 (Mode/Industry/Freshness) 检索，合并去重，并打权威标签。"""
    if WSA_MODE == "offline":
        return _tag_authority(mock_search(query, scene))
    if not (WSA_SECRET_ID and WSA_SECRET_KEY):
        raise RuntimeError("真实搜索未启用：请配置 WSA_SECRET_ID 和 WSA_SECRET_KEY")

    if not WSA_MULTIPATH:
        # 关闭多路时退化为单路
        return _tag_authority(_search_live(query, mode=2, cnt=WSA_MAX_RESULTS,
                                          freshness="y1",
                                          industry=("news" if scene == "rumor" else None)))

    paths = _PATHS_RUMOR if scene == "rumor" else (_PATHS_LATEST if scene == "latest" else _PATHS_AI)

    def _one(p):
        tag, mode, ind, fr = p
        res = _search_live(query, mode=mode, cnt=10, freshness=fr, industry=ind)
        for r in res:
            r["path"] = tag
        return res

    merged = []
    with ThreadPoolExecutor(max_workers=len(paths)) as ex:
        for res in ex.map(_one, paths):
            merged.extend(res)

    # 按 url 去重（保留首次出现，累计命中路径）
    seen = {}
    for r in merged:
        u = r.get("url", "")
        if u and u in seen:
            seen[u].setdefault("paths", [seen[u].get("path")])
            seen[u]["paths"].append(r.get("path"))
            continue
        r.setdefault("paths", [r.get("path")])
        seen[u] = r
    out = list(seen.values())
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return _tag_authority(out[:15])
