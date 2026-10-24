"""
元宝搜索查证工具 —— 配置中心
读取 .env，控制 online/offline 双模式。
比赛现场没网络/没 Key 时，把 WSA_MODE 设为 offline 即可用预置样例跑通全流程演示。
"""
import os
from dotenv import load_dotenv

load_dotenv()

# 运行模式：offline（预置样例，无需Key）| live（真实调用元宝搜索API）
WSA_MODE = os.getenv("WSA_MODE", "offline").lower()

# 腾讯云 API 密钥（live 模式需要）
WSA_SECRET_ID = os.getenv("WSA_SECRET_ID", "")
WSA_SECRET_KEY = os.getenv("WSA_SECRET_KEY", "")

# 若改用 MCP / HTTP Bearer 方式调用，可填这个
WSA_API_KEY = os.getenv("WSA_API_KEY", "")

# 单次查证返回的最大结果条数（10/20/30/40/50，极速版不支持Cnt）
WSA_MAX_RESULTS = int(os.getenv("WSA_MAX_RESULTS", "10"))

# 断言抽取用的混元模型（TokenHub 平台）。
# 注意：不同账号在 TokenHub 控制台开通的模型服务 ID 可能不同，常见的有 hy3 / hy3-preview。
# 本账号实测可用的是 hy3；若调用报「service ID 不存在」，请到控制台在线推理服务列表核对。
HUNYUAN_MODEL = os.getenv("HUNYUAN_MODEL", "hy3")

# TokenHub API Key（用于调用混元 hy3 抽取断言 / 意图识别 / 查询改写 / 语义核验 / 报告生成）。
# 注意：这是 TokenHub 控制台单独创建的 Key，不是腾讯云 SecretId/SecretKey。
# 未配置时，各 LLM 增强层自动回退到「启发式规则」，不影响主流程。
TOKENHUB_API_KEY = os.getenv("TOKENHUB_API_KEY", "")

# ———— 查证流水线各增强层开关（无 TokenHub Key 时全部自动回退，无需手动关）————
LLM_INTENT = os.getenv("LLM_INTENT", "true").lower() == "true"      # ① 意图识别与对齐
LLM_REWRITE = os.getenv("LLM_REWRITE", "true").lower() == "true"    # ② 查询改写
LLM_SEMANTIC = os.getenv("LLM_SEMANTIC", "true").lower() == "true"  # ⑤ 混元语义核验
LLM_FUSION = os.getenv("LLM_FUSION", "true").lower() == "true"      # ⑦ 内容融合与生成
WSA_MULTIPATH = os.getenv("WSA_MULTIPATH", "true").lower() == "true"  # ④ 多路信息源检索

# 单次查证最多对多少条断言跑「混元语义核验」（控成本/时延），其余走规则判定
SEMANTIC_MAX = int(os.getenv("SEMANTIC_MAX", "4"))

# ① Coder 代码核验（对数值型断言用 Python 沙箱核算）
CODE_VERIFY = os.getenv("CODE_VERIFY", "true").lower() == "true"
CODE_MAX = int(os.getenv("CODE_MAX", "3"))          # 单次查证最多对几条数值断言跑代码核验（控成本）
CODE_TIMEOUT = float(os.getenv("CODE_TIMEOUT", "4"))  # 沙箱子进程执行超时（秒）
# 代码核验阶段总时间预算（秒）：超时即放弃剩余断言，避免拖慢整条流水线。
# 实测：hy3 生成代码为推理模型，慢的一次要 15s+，无预算时单条失败会空耗 50s。
CODE_BUDGET = float(os.getenv("CODE_BUDGET", "28"))

# ③ 检索重规划：单条断言检索结果少于此数则触发「重规划回填」一次
REPLAN_MIN = int(os.getenv("REPLAN_MIN", "3"))
