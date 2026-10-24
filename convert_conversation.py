#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert a WorkBuddy session JSONL export into a clean Markdown dialogue file.

Usage:
    python convert_conversation.py <session.jsonl> <out.md>

Only `type=="message"` entries (role user / assistant) are kept.
Injected <system-reminder> / <user-prompt-submit-hook> blocks are stripped.
Consecutive same-role turns are merged into one block for readability.
Output uses CRLF line endings.
"""
import json
import re
import sys
from datetime import datetime, timezone, timedelta

SRC = sys.argv[1] if len(sys.argv) > 1 else None
OUT = sys.argv[2] if len(sys.argv) > 2 else "CONVERSATION.md"

# tz: Asia/Shanghai (+8)
TZ = timezone(timedelta(hours=8))


def strip_injected(text: str) -> str:
    """Remove system-injected blocks that are not part of the real dialogue."""
    text = re.sub(r"<system-reminder[\s\S]*?</system-reminder>", "", text)
    text = re.sub(r"<user-prompt-submit-hook[\s\S]*?</user-prompt-submit-hook>", "", text)
    text = re.sub(r"<user_query>([\s\S]*?)</user_query>", r"\1", text)
    # any leftover self-closing / empty system tags
    text = re.sub(r"<system-[\s\S]*?>", "", text)
    return text


# Redact common secret shapes so exported logs never leak live credentials
# (GitHub secret scanning blocks pushes that contain a real ghp_ token).
SECRET_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}"          # GitHub PATs
    r"|github_pat_[A-Za-z0-9_]{20,}"        # GitHub fine-grained PAT
    r"|AKID[A-Za-z0-9]{20,}"                # Tencent SecretId
    r"|sk-[A-Za-z0-9]{20,}"                 # OpenAI-style keys
    r"|[Tt]okenHub[A-Za-z0-9_\-]{12,}"      # TokenHub key hints
)


def redact_secrets(text: str) -> str:
    return SECRET_RE.sub("[REDACTED_SECRET]", text)


def extract_text(entry: dict) -> str:
    ctn = entry.get("content")
    if isinstance(ctn, str):
        return ctn
    if isinstance(ctn, list):
        parts = []
        for x in ctn:
            if isinstance(x, dict):
                parts.append(x.get("text") or x.get("input") or x.get("output_text") or x.get("content") or "")
        return "\n".join(p for p in parts if p)
    return ""


def conv_ts(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, TZ)
    except Exception:
        return None


def main():
    with open(SRC, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]

    # meta
    titles = [o.get("aiTitle") for o in rows if o.get("type") == "ai-title" and o.get("aiTitle")]
    ts_list = [o.get("timestamp") for o in rows if o.get("timestamp")]
    t0 = conv_ts(min(ts_list)) if ts_list else None
    t1 = conv_ts(max(ts_list)) if ts_list else None

    n_user = sum(1 for o in rows if o.get("type") == "message" and o.get("role") == "user")
    n_asst = sum(1 for o in rows if o.get("type") == "message" and o.get("role") == "assistant")

    # collect cleaned turns
    turns = []  # (role, text)
    for o in rows:
        if o.get("type") != "message":
            continue
        role = o.get("role")
        if role not in ("user", "assistant"):
            continue
        raw = extract_text(o)
        clean = redact_secrets(strip_injected(raw)).strip()
        # collapse excessive blank lines
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        if not clean:
            continue
        turns.append((role, clean))

    # merge consecutive same-role
    merged = []
    for role, text in turns:
        if merged and merged[-1][0] == role:
            merged[-1][1] += "\n\n---\n\n" + text
        else:
            merged.append([role, text])

    # build markdown
    L = []
    L.append("# AI Detective - Development Conversation Log")
    L.append("")
    L.append("> Source session: `7fff3beb-52aa-439e-880c-3caa2dd7cba1`  ")
    L.append("> Topic: " + (titles[0] if titles else "AI Detective (ai-detective) build") + "  ")
    L.append("> Period: " + (t0.strftime("%Y-%m-%d %H:%M") if t0 else "?") +
             " ~ " + (t1.strftime("%Y-%m-%d %H:%M") if t1 else "?") + " (UTC+8)  ")
    L.append("> Turns: %d user questions / %d assistant replies (merged into %d blocks)" % (n_user, n_asst, len(merged)))
    L.append("")
    L.append("---")
    L.append("")

    idx = 0
    for role, text in merged:
        idx += 1
        if role == "user":
            L.append("## 👤 User  (%d)" % idx)
        else:
            L.append("## 🤖 Assistant  (%d)" % idx)
        L.append("")
        L.append(text)
        L.append("")

    out = "\n".join(L).rstrip() + "\n"
    with open(OUT, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(out)

    print("OK -> %s" % OUT)
    print("  titles:", titles)
    print("  period:", t0, "~", t1)
    print("  raw turns(user/asst):", n_user, n_asst, "| merged blocks:", len(merged))
    print("  size: %.1f KB" % (len(out.encode("utf-8")) / 1024))


if __name__ == "__main__":
    if not SRC:
        print("usage: python convert_conversation.py <session.jsonl> <out.md>")
        sys.exit(1)
    main()
