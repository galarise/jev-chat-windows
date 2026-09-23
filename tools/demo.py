# -*- coding: utf-8 -*-
"""端到端冒烟：两套真实对话（恋爱 + 职场）各跑一遍完整链，打印判断 + 排好序的候选。

    判断层（默认 OpenRouter）端点可用环境变量覆盖：
        JEVC_JUDGE_URL    端点，如 https://jevtypesafeai.com/api/v1/decide
        JEVC_JUDGE_MODEL  模型 id，该端点只认 jev-latest
        OPENROUTER_API_KEY  判断层 key
    起草层 key 从 OPENCODE_API_KEY 读（默认走 opencode-go）。

    python tools/demo.py                  # 两套都跑
    python tools/demo.py --sample work    # 只跑职场样例
    python tools/demo.py --sample romance # 只跑恋爱样例

起草想走 DeepSeek 直连就把下面 PROVIDER 改成 "deepseek"，并设好 DEEPSEEK_API_KEY。
"""
from __future__ import annotations

import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core.engine import analyze
from core.jev_client import JevError

SAMPLES = {
    "romance": {
        "name": "恋爱样例（忠诚试探）",
        "messages": [
            ("her", "你今天是不是又忘了我跟你说过什么？"),
            ("me", "记得，你先别提示我，让我自己说。"),
            ("her", "那你说。"),
            ("me", "等一下，我想说完整一点。"),
            ("her", "你最好是。"),
        ],
        "relationship": "romantic partners",
        "expect": "true_intent≈confirm_you_care, best_action≈check_history, danger_level 中高档",
    },
    "work": {
        "name": "职场样例（领导甩锅）",
        "messages": [
            ("her", "这个需求上周就说清楚了，怎么你们交付的东西跟方案对不上？"),
            ("me", "我看下是哪部分对不上"),
            ("her", "别看下看下的，这周五就要汇报了，现在出问题是你这块的责任"),
            ("me", "当时方案是你们定的，我们按那个做的"),
            ("her", "方案定完你们没提异议，现在出了问题再说这个没意义"),
        ],
        "relationship": "colleague, my manager",
        "expect": "domain≈work, true_intent≈blame_shift, best_action∈{ask_clarify, take_responsibility, check_history}, "
                  "且不出现恋爱专属选项（apologize/confirm_you_care/vent_anger）",
    },
}
PROVIDER = "opencode-go"  # 或 "openrouter"（OPENROUTER_API_KEY）/ "deepseek"（DEEPSEEK_API_KEY）

_ROMANCE_ONLY = {"apologize", "confirm_you_care", "vent_anger", "care"}
_WORK_ONLY = {"blame_shift", "escalate_explicitly", "take_responsibility"}


def fmt(name: str, ans: dict) -> str:
    t = ans.get("type")
    if t == "noul":
        return f"{name}: {ans.get('noul'):.2f}"
    if t == "choice":
        return f"{name}: {ans.get('choice')} (conf {ans.get('confidence'):.2f})"
    if t == "score":
        return f"{name}: {ans.get('score'):.1f}/9 (conf {ans.get('confidence'):.2f})"
    return f"{name}: {ans}"


def run(key: str) -> int:
    sample = SAMPLES[key]
    print("=" * 60)
    print(sample["name"])
    print("=" * 60)
    print("对话:")
    for w, t in sample["messages"]:
        print(f"  {w}: {t}")
    try:
        r = analyze(sample["messages"], sample["relationship"], provider=PROVIDER)
    except JevError as e:
        print(f"\n失败: {e}")
        return 1

    print(f"\n域路由: {r['domain']} | 召回策略: "
          f"{[s['scenario_code'] for s in r['strategies']] or '（无）'}")
    print("\n判断:")
    for name in ("domain_check", "literal_question", "true_intent", "danger_level",
                 "should_reply_now", "best_action", "she_needs", "tension_resolved"):
        if name in r["answers"]:
            print("  " + fmt(name, r["answers"][name]))

    print("\n候选（Jev 排序，★ = 推荐）:")
    scores = r.get("scores")
    for i, c in enumerate(r["candidates"]):
        pct = f"  {scores[i]:.0%}" if scores else ""
        print(f"  {'★' if i == r['best_index'] else ' '} {c}{pct}")

    u = r["usage"]
    if u:
        print(f"\nusage: in={u.get('input_tokens')} out={u.get('output_tokens')} "
              f"cost=${u.get('cost')}")

    # 域不串断言
    answers_str = str(r["answers"])
    if key == "work":
        assert "apologize" not in str(r["answers"].get("best_action", "")), \
            "职场样例 best_action 出现恋爱专属 apologize"
        assert r["domain"] == "work", f"职场样例被路由到 {r['domain']}"
    else:
        assert r["domain"] == "romance", f"恋爱样例被路由到 {r['domain']}"
    print(f"\n期望核对: {sample['expect']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", choices=list(SAMPLES), help="只跑一个样例")
    a = ap.parse_args()
    keys = [a.sample] if a.sample else list(SAMPLES)
    rc = 0
    for k in keys:
        rc = run(k) or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
