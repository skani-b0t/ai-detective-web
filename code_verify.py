"""
① Coder 数值核验（受 DeerFlow Coder 启发，但做成安全沙箱版）
对含数字/比例/日期/计算的事实断言，让混元生成一段纯计算的 Python 代码，
在独立的子进程里执行（超时即杀掉），把"声明值"与"计算值"做比对，
输出 {claimed, computed, ok, detail} 作为一条独立证据。

安全设计（内容安全优先）：
- 生成的代码先过白名单校验：只允许 import 数学/统计/日期等纯计算标准库。
- 命中任何受限语法（open/eval/exec/os/sys/网络/文件/系统调用…）直接拒绝执行。
- 真正执行时用 subprocess 起一个独立子进程，超时(CODE_TIMEOUT)由 Python 强制杀掉，
  即使代码写出死循环也不会拖垮主服务。
- 无 TOKENHUB_API_KEY 或模型限流返回 None 时，本层自动跳过，不影响主流程。
"""
import re
import sys
import json
import os
import ast
import tempfile
import subprocess

from config import TOKENHUB_API_KEY, CODE_TIMEOUT
from hunyuan import chat

# 允许导入的纯计算标准库（白名单）
_ALLOW_IMPORTS = {
    "math", "statistics", "decimal", "fractions", "datetime",
    "re", "itertools", "collections", "random",
}
# 任何命中即拒绝执行的受限片段
_BLOCK = [
    "__", "open(", "eval(", "exec(", "compile(", "input(", "exit(", "quit(",
    "breakpoint(", "globals(", "locals(", "getattr(", "setattr(", "__import__",
    "os.", "sys.", "subprocess", "socket", "requests", "shutil", "pathlib",
    "import os", "import sys", "import subprocess", "import socket",
]


def is_numeric_claim(text):
    """判断断言是否含数值型信息（数字 + 单位/比较词）。"""
    if not re.search(r"\d", text or ""):
        return False
    if re.search(r"%|％|倍|元|万|亿|公斤|千克|克|斤|度|岁|分|年|月|日|km|kg|cm|mm|小时|分钟|％", text or ""):
        return True
    if re.search(r"增长|下降|提高|降低|减少|增加|占比|排名|超过|以上|以下|等于|达|为", text or ""):
        return True
    return False


def is_computable_claim(text):
    """判断数值断言是否「可计算」（两个量之间存在运算/换算关系）。

    区分两类数值断言，避免把预算浪费在算不出来的事实上：
    - 可计算：含 ≥2 个数值 + 明确的比例/变化关系
      （例：「销量同比增长300%，从80万件增至200万件」→ 实为 +150%，可算且能抓出矛盾）
    - 纯事实：分数线/气温/价格/日期等只能靠检索核对，算不出来
      （例：「录取分数线680分」→ 交给信源交叉验证，不进沙箱）
    """
    if not is_numeric_claim(text):
        return False
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    rel = re.search(
        r"占比|百分比|%|％|倍|增长|下降|提高|降低|减少|增加|平均|总计|合计|换算|"
        r"相当于|增至|增至|从[^，。]*?到|从[^，。]*?增至|每|增速|涨幅|跌幅",
        text or "")
    return bool(len(nums) >= 2 and rel)


def _gen_code(claim):
    """让混元生成一段只做纯计算的 Python，print 出一行 JSON。失败返回 None。"""
    if not TOKENHUB_API_KEY:
        return None
    prompt = (
        "你是一个「数值核验」代码生成器。给定一条待查证的事实断言（通常含数字、比例、日期或计算），"
        "请生成一段 Python 代码，仅用基础算术与 math 标准库，计算出该断言里的关键数值，"
        '并 print 出一行 JSON：\n'
        '{"claimed": <断言中声称的数值或可表述>, "computed": <你计算得到的数值>, '
        '"ok": <bool，计算值是否支撑声称>, "detail": "<一句话说明计算过程>"}\n'
        "要求：\n"
        "- 不要使用任何网络、文件、系统调用；只允许 import math / statistics / decimal / fractions / datetime。\n"
        "- 只输出一个 python 代码块，禁止任何解释文字。\n"
        f"待核验断言：{claim}"
    )
    # 两点针对 hy3 推理模型的调优（实测）：
    # 1) max_tokens 过小会让「思考」烧光预算导致空返回，进而触发放大重试（慢 3 倍）→ 直接给足；
    # 2) 生成代码实测要 40s+，默认 30s 的读超时会被掐断 → 这里放宽到 90s。
    # 两者都指向同一个结论：hy3 要「给足预算 + 给足时间」，别用普通模型的参数直觉。
    text = chat(prompt, temperature=0.1, max_tokens=2000, timeout=90, retries=2)
    if not text:
        return None
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    code = m.group(1).strip() if m else text.strip()
    return code or None


def _sanitize(code):
    """校验代码安全性，返回 (ok, reason)。"""
    for line in code.splitlines():
        s = line.lstrip()
        m = re.match(r"^(?:import\s+(\w+)|from\s+(\w+)\s+import)", s)
        if m:
            mod = m.group(1) or m.group(2)
            if mod not in _ALLOW_IMPORTS:
                return False, f"禁止导入模块: {mod}"
    for b in _BLOCK:
        if b in code:
            return False, f"检测到受限语法: {b}"
    return True, ""


def _run_code(code, timeout=CODE_TIMEOUT):
    """在独立子进程执行代码，解析 stdout 里的 JSON。失败/超时返回 None。"""
    ok, reason = _sanitize(code)
    if not ok:
        return {"error": reason, "blocked": True}
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code + "\n")
            path = f.name
        r = subprocess.run(
            [sys.executable, path],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (r.stdout or "").strip()
        if not out:
            return {"error": "代码无输出", "stderr": (r.stderr or "")[:200]}
        # 兼容模型输出严格 JSON 或 Python 风格 dict 字面量
        line = out.splitlines()[-1].strip()
        try:
            data = json.loads(line)
        except Exception:
            try:
                data = ast.literal_eval(line)
            except Exception:
                m = re.search(r"\{.*\}", out, re.DOTALL)
                if not m:
                    return {"error": "输出非 JSON", "raw": out[:200]}
                try:
                    data = ast.literal_eval(m.group(0))
                except Exception:
                    return {"error": "输出非 JSON", "raw": out[:200]}
        return data
    except subprocess.TimeoutExpired:
        return {"error": "执行超时，已终止", "timeout": True}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def verify_numeric(claim):
    """对单条数值断言做代码核验。非数值 / 无 Key / 任意失败 → 返回 None（自动跳过）。"""
    if not is_numeric_claim(claim):
        return None
    code = _gen_code(claim)
    if not code:
        return None
    res = _run_code(code, CODE_TIMEOUT)
    if not res or res.get("error"):
        return None
    return {
        "numeric": True,
        "claimed": res.get("claimed"),
        "computed": res.get("computed"),
        "ok": bool(res.get("ok")),
        "detail": res.get("detail", ""),
        "engine": "hunyuan-coder",
    }
