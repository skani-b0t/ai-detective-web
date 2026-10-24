"""
可信源矩阵（③ 之一，配合 ④ 多路检索与 ⑤ 多层过滤）
对每条检索结果的来源做「权威度分级」，供后续加权与交叉验证使用。

分级逻辑（classify_domain）：
  tier 3  权威白名单  —— 政府/央媒/国际组织/顶级学术/官方统计，强可信
  tier 2  高可信      —— 高校(.edu)、权威性较强的媒体、或 wsa 自带 authority_level>=4
  tier 1  一般        —— 普通合规站点
  tier 0  低质/疑似   —— 营销号特征、黑名单域、或 wsa authority_level==0 且无品牌

注意：域名匹配用「注册域后缀包含」判断，覆盖子域。
"""
import re

# 权威白名单（域后缀或完整域）
AUTHORITY_WHITELIST = [
    # 中国政府与央媒
    "gov.cn", "people.com.cn", "xinhuanet.com", "news.cn", "cctv.com", "cntv.cn",
    "chinanews.com.cn", "thepaper.cn", "stats.gov.cn", "mof.gov.cn", "edu.cn",
    "who.int", "un.org", "nih.gov", "cdc.gov", "fda.gov", "nasa.gov", "europa.eu",
    "nature.com", "science.org", "sciencedirect.com", "springer.com", "ieee.org",
    "acm.org", "wiley.com", "ox.ac.uk", "mit.edu", "harvard.edu", "tsinghua.edu.cn",
    "pku.edu.cn", "cas.cn", "oxfordjournals.org", "bmj.com", "thelancet.com",
    "reuters.com", "apnews.com", "bbc.com", "nytimes.com", "who.int",
]

# 低质 / 谣言高发的黑名单域（示例，可按需扩充）
BLACKLIST = [
    "rumor", "cheat", "fake-news", "conspiracy", "hoax", "qianbao", "liangzuan",
]

# 营销号特征关键词（命中则降权）
PROMO_HINTS = ["加盟", "招商", "秒杀", "限时", "爆款", "代购", "私聊", "加微信", "商城", "赚"]


def _registered_domain(url):
    m = re.search(r"https?://([^/]+)/?", url or "")
    host = (m.group(1) if m else (url or "")).lower()
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def classify_domain(url, authority_level=0):
    """返回 dict: tier(0-3) / label / whitelist(bool) / blacklist(bool) / promo(bool)。"""
    host = _registered_domain(url)
    promo = any(h in (url or "") for h in PROMO_HINTS)
    black = any(b in host for b in BLACKLIST)
    white = any(host == w or host.endswith("." + w) or ("." + w) in host
                for w in AUTHORITY_WHITELIST)

    if black:
        return {"tier": 0, "label": "低质/疑似谣言源", "whitelist": False, "blacklist": True, "promo": promo}
    if white:
        return {"tier": 3, "label": "权威信源", "whitelist": True, "blacklist": False, "promo": promo}
    if authority_level and authority_level >= 4:
        return {"tier": 2, "label": "高可信", "whitelist": False, "blacklist": False, "promo": promo}
    if host.endswith(".edu") or host.endswith(".edu.cn") or authority_level >= 3:
        return {"tier": 2, "label": "高可信(学术/机构)", "whitelist": False, "blacklist": False, "promo": promo}
    if promo:
        return {"tier": 0, "label": "营销号特征", "whitelist": False, "blacklist": False, "promo": True}
    return {"tier": 1, "label": "一般信源", "whitelist": False, "blacklist": False, "promo": promo}


def authority_weight(tier):
    """tier → 权重系数，用于支持度加权。"""
    return {3: 1.0, 2: 0.75, 1: 0.4, 0: 0.1}.get(tier, 0.4)
