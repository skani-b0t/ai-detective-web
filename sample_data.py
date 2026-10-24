"""
离线演示用预置数据。
offline 模式下 wsa_client 不调真实 API，而是从这里拿结果，保证现场无网/无Key也能演示全流程。
字段已与真实 SearchPro 返回对齐：title/url/summary/score/publish_time/source/authority_level。
建议比赛前用真实场景替换成你提前跑好的缓存结果（见 verifier 的缓存思路）。
"""


def mock_search(query, scene="ai"):
    """返回与真实 SearchPro 同构的样例结果。"""
    # 路演高光案例：AI 最爱胡编的「某高校录取分数线」
    if "录取分数线" in query or ("录取" in query and "分数" in query):
        return [
            {
                "title": "关于2026年普通高校招生录取分数线的官方说明",
                "url": "https://www.moe.gov.cn/jyb_xxgk/xxgk_jyta/jyta_zss/",
                "summary": "教育部未统一发布所谓「2026各校录取分数线」，各省分数线由省级招办在投档后公布，任何提前列出的「分数线」均非官方数据。",
                "score": 0.92,
                "publish_time": "2026-06-12",
                "source": "教育部",
                "authority_level": 4,
            },
            {
                "title": "网传「XX大学2026录取分数线680分」为不实信息",
                "url": "https://www.piyao.org.cn/2026/06/15/c_123.html",
                "summary": "经核查，网传「XX大学2026录取分数线680分」系往年数据篡改，该校招生办已辟谣。",
                "score": 0.88,
                "publish_time": "2026-06-15",
                "source": "中国互联网联合辟谣平台",
                "authority_level": 4,
            },
            {
                "title": "2026年各省高考录取分数线查询入口",
                "url": "https://gaokao.chsi.com.cn/",
                "summary": "考生应通过本省教育考试院官方渠道查询投档分数线，切勿轻信第三方预估。",
                "score": 0.71,
                "publish_time": "2026-06-23",
                "source": "学信网",
                "authority_level": 3,
            },
        ]

    # 通用：基于 query 拼出看起来真实的样例，保证任意输入都能跑出结果
    q = query.strip()
    return [
        {
            "title": f"关于「{q}」的权威说明（样例）",
            "url": "https://www.gov.cn/zhengce/",
            "summary": f"针对「{q}」，相关主管部门发布的解读显示，该说法需要结合具体语境判断，建议以官方发布为准。",
            "score": 0.74,
            "publish_time": "2026-09-01",
            "source": "中国政府网",
            "authority_level": 4,
        },
        {
            "title": f"「{q}」网络流传说法核查（样例）",
            "url": "https://www.piyao.org.cn/",
            "summary": f"对「{q}」的核查发现，部分自媒体存在夸大表述，原始研究结论与之存在差异。",
            "score": 0.61,
            "publish_time": "2026-08-20",
            "source": "辟谣平台",
            "authority_level": 3,
        },
        {
            "title": f"「{q}」相关报道汇总（样例）",
            "url": "https://news.qq.com/",
            "summary": f"多家媒体就「{q}」进行了报道，信源相互独立，结论基本一致。",
            "score": 0.55,
            "publish_time": "2026-07-10",
            "source": "腾讯新闻",
            "authority_level": 3,
        },
    ]
