# AI 审讯室 · 元宝搜索查证工具

为 **WaytoAGI「1024 超能 Coding 周末 · 侦探赛道 × 元宝搜索」** 打造的一站式查证工具。

> 主题一（主场景：审 AI 查幻觉）+ 主题二（场景 Tab：审谣言）+ 主题三（输出层：查证报告卡）
> 一个作品 · 一个代码库 · 两个场景 Tab · 一张可分享的报告卡。

## 它能做什么
输入一段文字（AI 回答 / 家族群消息），工具会：
1. **拆断言** —— 把长文拆成可查证的事实性断言；
2. **搜信源** —— 逐条调用腾讯云「元宝搜索 API（wsa / SearchPro）」检索多方来源；
3. **交叉核对** —— 比对断言与信源，给出四态判定：
   - ✅ 有据可查　⚠️ 存疑　❌ 与信源冲突　🔍 查无来源
4. **出报告卡** —— 输出带一致性评分、独立信源数、逐条证据链的可分享海报。

## 快速开始
```bash
pip install -r requirements.txt
cp .env.example .env        # offline 模式无需填 Key 即可演示
python app.py               # 打开 http://127.0.0.1:5000
```
- 点「🎬 一键示例」即可看到完整效果（无需任何配置）。
- 点「📸 生成分享海报」把报告卡存成图片。

## 切换到真实 API（live 模式）
1. 腾讯云控制台 → 访问管理 → API 密钥，拿到 SecretId / SecretKey；
2. 开通「元宝搜索」服务（侦探赛道每人送 6000 次免费调用）；
3. 在 `.env` 中设置：
   ```
   WSA_MODE=live
   WSA_SECRET_ID=你的ID
   WSA_SECRET_KEY=你的KEY
   ```
4. 重启 `python app.py`，查证即走真实联网检索。

## 接入大模型（强烈建议，提升完成度）
`claim_extractor.py` 的 `llm_extract()` 默认调用 **腾讯混元 Hy3（TokenHub 平台，OpenAI 兼容协议）**，
- 断言抽取更精准（抓得住隐含事实）；
- 可把 `verifier.py` 的「重叠度」判定升级为「语义一致性」判断，准确率大幅提升。

> ⚠️ **重要变更（2026-06-22 起）**：旧版腾讯云混元 ChatCompletions API（hunyuan-turbo / -standard / -pro…）已全面下线，
> 新版统一迁移到 **TokenHub**，使用的是独立的 **TokenHub API Key**（不是腾讯云 SecretId/SecretKey）。

接入方式：
1. 在 TokenHub 控制台 **先用在线推理开通模型服务**（免费体验/后付费），再创建 API Key
   （https://console.cloud.tencent.com/tokenhub/apikey）；模型服务 ID 以控制台「在线推理服务列表」为准，本项目实测为 `hy3`（部分账号为 `hy3-preview`），可通过 `HUNYUAN_MODEL` 调整。
2. 在 `.env` 填入 `TOKENHUB_API_KEY=你的TokenHubKey`；报告卡技术栈会自动显示「🤖 混元已抽取 N 条断言」。
3. **不接**：留空则自动用「启发式规则」抽取，技术栈显示「📐 规则抽取」，功能完整但抽取精度略低。

前端已把 `use_llm` 默认置 true，配好 Key 即自动启用混元。

## 亮点
- 🔴 **审 AI 幻觉**：全场差异化角度，路演自带反差梗；
- 🟢 **审谣言 Tab**：覆盖最易共鸣的真实场景；
- 🟣 **独立信源检测**：多来源一致度 + 去重域名，避免「五家媒体其实同一出处」的假一致；
- 🖼️ **报告卡可分享**：路演结束即发朋友圈拉票；
- 🛟 **离线兜底**：联网失败也不怕，提前缓存结果即可演示。

## 目录结构
```
ai-detective/
├── app.py              # Flask 入口（/、/verify、/demo）
├── config.py           # 配置：offline/live、凭证、条数
├── wsa_client.py       # 元宝搜索 SearchPro 封装（离线/在线双实现）
├── claim_extractor.py  # 断言抽取（启发式 + LLM 钩子）
├── verifier.py         # 核对引擎：判定四态 + 报告汇总
├── sample_data.py      # 离线预置样例（含路演案例）
├── templates/index.html
├── static/{style.css, app.js}
├── requirements.txt
└── .env.example
```
