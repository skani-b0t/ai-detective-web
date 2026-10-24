# AI 侦探社部署说明

## 一、Render 免费版（公网，推荐）

这台机器上的项目已经整理成「推上去就能跑」的状态：

- `render.yaml` 固定了 `plan: free`、`region: singapore`、健康检查 `/api/status`；
- `requirements.txt` 补齐了 `pillow`（截图 OCR 压缩用，缺了会静默降级）；
- `app.py` 加了 `ProxyFix`，否则限流会把所有访客当成同一个 IP，第 12 次查询起全站 429；
- `.env` 已在 `.gitignore` 中，密钥只进托管平台的 Environment Variables。

部署方式（命令行，无需在网页点）：

```powershell
$env:GH_TOKEN="ghp_xxx"        # GitHub PAT，scope=repo
$env:GH_USER="你的用户名"
$env:RENDER_KEY="rnd_xxx"      # Render → Account Settings → API Keys
$env:WSA_SECRET_ID="AKID..."
$env:WSA_SECRET_KEY="..."
python ..\push_and_deploy.py   # 建库、推代码、建服务、填环境变量、等构建
```

成功后脚本会打印 `PUBLIC URL: https://xxx.onrender.com`。

### 环境变量清单

| 变量 | 说明 |
| --- | --- |
| `WSA_SECRET_ID` / `WSA_SECRET_KEY` | 腾讯云密钥，元宝搜索 + OCR + 混元共用 |
| `WSA_MODE=live` | 真实联网检索；填 `offline` 则走内置样例 |
| `TOKENHUB_API_KEY` | 可选，填了断言抽取走混元 hy3，留空退化为规则抽取 |
| `PUBLIC_RATE_LIMIT=true` | 公网必开 |
| `SEARCHES_PER_CLIENT_DAY=12` | 每个 IP 每天可查证次数 |
| `SEARCHES_TOTAL_DAY=200` | 全站每天总次数上限，防止密钥被刷爆 |
| `CODE_VERIFY=false` | 关掉代码沙箱核验，省时间（免费实例 CPU 很弱） |

### 免费实例的两个已知特性

1. **闲置 15 分钟会休眠**，之后第一次访问要等约 50 秒唤醒。发给别人前自己先点一次。
2. 单实例 512MB 内存，`gunicorn` 固定 `--workers 1 --threads 8`，因为限流和缓存都在进程内存里，多 worker 会各算各的。

## 二、本地运行

```powershell
$env:PORT="5002"
python app.py
```

## 三、密钥安全

- 不要把 `.env` 提交到 GitHub（已加入 `.gitignore`）。
- 本机 `.env` 里是明文密钥，建议在腾讯云控制台按需轮换，或在 Render 面板改为只读密钥。
- 交付用的 GitHub token / Render API key 在部署完成后可以直接撤销，不影响已运行的网站。
