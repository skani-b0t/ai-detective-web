"""
查证结果缓存（内存 + 磁盘双层）

目的：现场演示时，演练过的示例秒出结果，避免等待完整流水线（实测 60–180 秒）。
- 内存层：同一次进程内命中，纳秒级；
- 磁盘层：存 .cache/<key>.json，服务重启后依然命中（比赛现场可能重启）。

缓存只复用「相同 scene + 相同输入文本」的查证结果，是标准工程实践；
不参与判定逻辑，不影响任何查证算法。
"""
import hashlib
import json
import os
import threading

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
os.makedirs(_BASE, exist_ok=True)

_mem = {}
_lock = threading.Lock()


def make_key(scene, text):
    raw = f"{scene}||{(text or '').strip()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get(scene, text):
    """命中返回 dict，未命中返回 None。"""
    k = make_key(scene, text)
    with _lock:
        if k in _mem:
            return _mem[k]
    path = os.path.join(_BASE, k + ".json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            with _lock:
                _mem[k] = d
            return d
        except Exception:  # noqa: BLE001
            return None
    return None


def put(scene, text, report):
    """写入缓存（内存 + 磁盘）。report 必须是可 JSON 序列化的 dict。"""
    k = make_key(scene, text)
    with _lock:
        _mem[k] = report
    try:
        path = os.path.join(_BASE, k + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        print(f"[cache] 写入磁盘缓存失败（不影响主流程）：{e}")


def stats():
    """返回已缓存条目数（内存层 + 磁盘层去重后近似值）。"""
    try:
        disk = len([f for f in os.listdir(_BASE) if f.endswith(".json")])
    except Exception:  # noqa: BLE001
        disk = 0
    with _lock:
        return {"memory": len(_mem), "disk": disk}


def clear():
    """清空缓存（换示例/换环境时用）。"""
    with _lock:
        _mem.clear()
    try:
        for f in os.listdir(_BASE):
            if f.endswith(".json"):
                os.remove(os.path.join(_BASE, f))
    except Exception:  # noqa: BLE001
        pass
