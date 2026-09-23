# -*- coding: utf-8 -*-
"""策略导入：gaoqingshang-skill（MIT）23 个职场场景 → strategies 表。

用法（项目根跑，PYTHONPATH=.）：
  python tools/import_strategies.py --clone     # 克隆/更新源仓库并导入（中文话术直接入库）
  python tools/import_strategies.py --llm       # --clone 基础上再用 DeepSeek 批量提炼 summary_en/guidance
  python tools/import_strategies.py --check     # 只打印统计：条数、字段非空率、抽查 3 条
  python tools/import_strategies.py --manual F  # 用手填 JSON 文件导入（LLM 失败兜底）

源仓库固定 MIT（https://github.com/wanghoween-design/gaoqingshang-skill），话术原文可入库。
summary_en 是英文（Jev 口径：instructions/criteria 英文、state 内容可中文），LLM 不跑就退规则拼接
（title + 底层逻辑首条拼一句英文，能用但糙——优先 --llm）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from core import store
except ImportError:
    import store  # 直接当脚本跑

REPO_URL = "https://github.com/wanghoween-design/gaoqingshang-skill.git"
REPO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "gaoqingshang-skill")
FILES = ["scenarios_leader.md", "scenarios_colleague.md", "scenarios_subordinate.md",
         "scenarios_government.md", "scenarios_crisis.md"]

# 难度星级 → 该场景合理适用的危险等级区间（danger_level 0-9）。
# ⭐=普通沟通，⭐⭐⭐⭐=高危冲突；区间宁宽勿窄，召回侧还会按对话实际 danger 过滤。
DIFFICULTY_DANGER = {1: (0, 4), 2: (0, 6), 3: (2, 8), 4: (4, 9)}


def clone() -> None:
    if os.path.isdir(os.path.join(REPO_DIR, ".git")):
        print("[clone] already exists:", REPO_DIR)
        return
    subprocess.run(["git", "clone", "--depth", "1", REPO_URL, REPO_DIR], check=True)


# ---------------------------------------------------------------- 解析器

_H_SCENARIO = re.compile(r"^## (S\d+)：(.+)$", re.M)
_H_DIFFICULTY = re.compile(r"难度等级[:：]\s*\|?\s*([⭐]+)")
_H_BLOCK = re.compile(r"^\*\*(.+?)：?\*\*", re.M)


def _difficulty(line: str) -> int:
    return max(1, len(_H_DIFFICULTY.search(line).group(1)) if _H_DIFFICULTY.search(line) else 2)


def _section(text: str, start_pat: str, next_headers: tuple[str, ...]) -> str:
    """截取 start_pat 到下一个 ###/## 标题之间的内容。"""
    m = re.search(start_pat, text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search("|".join(r"^#{2,3} " + h for h in next_headers), rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def _strip_md(text: str) -> str:
    """Markdown 压成可读文本：表格行用「；」连接，引用符号剥掉，空白压一行。"""
    text = re.sub(r"^>\s*", "", text, flags=re.M)
    text = re.sub(r"^#{3,}\s*", "", text, flags=re.M)
    lines = []
    for ln in text.splitlines():
        ln = ln.strip().strip("|").replace("|", "；")
        if ln and ln not in ("---", "――"):
            lines.append(ln)
    return re.sub(r"\n{2,}", "\n", "\n".join(lines)).strip()


def parse_scenario(block: str) -> dict | None:
    """一个 ## Sxx 场景块 → 一条策略行。字段照 store.upsert_strategy 的 schema。"""
    m = _H_SCENARIO.match(block)
    if not m:
        return None
    code, title = m.group(1), m.group(2).strip()

    diff_m = re.search(r"难度等级.*", block)
    stars = len(re.findall("⭐", diff_m.group(0))) if diff_m else 2
    d_min, d_max = DIFFICULTY_DANGER.get(stars, (0, 6))

    desc = _strip_md(_section(block, r"### 场景描述", ("错误示范",)))
    bad = _strip_md(_section(block, r"### 错误示范", ("高情商",)))
    answer = _section(block, r"### 高情商标准答案", ("底层逻辑",))

    # 稳妥型/进阶型：answer 里每个 **【xxx】** 小节下的 **稳妥型：** 后跟引用块
    def _script(kind: str) -> str:
        # 抓该 kind 之后到下一个 ** 类型标签或 ### 之间的所有 > 引用行
        pat = re.compile(
            r"\*\*" + kind + r"[：:]\*\*(.*?)(?=\*\*[^*]+[：]?\*\*|\Z)", re.S)
        got = []
        for seg in pat.finditer(answer):
            got += [ln.lstrip("> ").strip() for ln in seg.group(1).splitlines()
                    if ln.strip().startswith(">")]
        return " / ".join(g for g in got if g)[:900]

    logic = _strip_md(_section(block, r"### 底层逻辑", ("万能句式",)))
    universal = _strip_md(_section(block, r"### 万能句式", ("适用环境",)))
    env = _strip_md(_section(block, r"### 适用环境差异", ()))

    # summary_en 规则兜底：场景名 + 逻辑首条 直译拼接（--llm 会覆盖成更准的）
    logic_first = logic.split("\n")[0] if logic else desc
    summary_en = f"{code}: {title} — {logic_first}"[:260]

    return {
        "domain": "work",
        "scenario_code": code,
        "title": f"{code} {title}",
        "summary_en": summary_en,
        "guidance": (desc + "\n" + logic).strip()[:1200],
        "safe_script": _script("稳妥型") or None,
        "advanced_script": _script("进阶型") or None,
        "universal_lines": universal or None,
        "bad_example": bad or None,
        "danger_min": d_min,
        "danger_max": d_max,
        "env_notes": env or None,
        "source": "gaoqingshang",
    }


def parse_all(repo_dir: str) -> list[dict]:
    out = []
    for name in FILES:
        path = os.path.join(repo_dir, "references", name)
        if not os.path.isfile(path):
            print("[warn] missing:", path)
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # 前瞻切分：在每个 "## Sxx：" 前下刀，块本身含标题，整块喂 parse_scenario
        blocks = re.split(r"(?=^## S\d+：)", text, flags=re.M)
        for block in blocks:
            if not block.startswith("## S"):
                continue
            row = parse_scenario(block)
            if row:
                out.append(row)
    return out


# ---------------------------------------------------------------- LLM 提炼

_LLM_SYS = (
    "你是双语沟通策略编辑。输入是一批中文职场沟通场景（编号+名称+打法要点）。"
    "为每个场景输出一行 JSON：{\"code\":..., \"summary_en\": 一句话英文摘要(格式 'S04: Handling "
    "{title-in-english} in a {env} setting — {one-line tactic}.', ≤180 chars), "
    "\"guidance\": 中文两三句打法要点(从原文提炼，不是抄原文)}. "
    "只输出 JSON 数组，共 N 行，别的不写。"
)


def llm_refine(rows: list[dict]) -> int:
    """DeepSeek 批量提炼 summary_en + guidance。失败打印原因返回 0，原行保留。"""
    try:
        from core import jev_client as jc
        key = jc._api_key("DEEPSEEK_API_KEY")
    except Exception as e:
        print("[llm] no DEEPSEEK_API_KEY, skip:", e)
        return 0
    items = [{"code": r["scenario_code"], "title": r["title"],
              "logic": (r["guidance"] or "")[:400]} for r in rows]
    body = {"model": "deepseek-flash",
            "messages": [{"role": "system", "content": _LLM_SYS},
                         {"role": "user",
                          "content": json.dumps(items, ensure_ascii=False)}],
            "temperature": 0.3, "max_tokens": 8000, "stream": False}
    import urllib.request
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        arr = json.loads(re.sub(r"^```(?:json)?|```$", "",
                                data["choices"][0]["message"]["content"].strip(),
                                flags=re.M).strip())
        by_code = {a.get("code"): a for a in arr if isinstance(a, dict)}
    except Exception as e:
        print("[llm] failed, keeping rule-based summaries:", e)
        return 0
    n = 0
    for r in rows:
        hit = by_code.get(r["scenario_code"])
        if hit and hit.get("summary_en"):
            r["summary_en"] = hit["summary_en"].strip()
            if hit.get("guidance"):
                r["guidance"] = hit["guidance"].strip()
            n += 1
    print(f"[llm] refined {n}/{len(rows)}")
    return n


def manual_import(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    for row in rows:
        store.upsert_strategy(row)
    print(f"[manual] imported {len(rows)} rows")


def do_import(llm: bool) -> None:
    store.init()
    rows = parse_all(REPO_DIR)
    if not rows:
        sys.exit("[import] no scenario parsed — check repo layout")
    if llm:
        llm_refine(rows)
    for row in rows:
        store.upsert_strategy(row)
    print(f"[import] upserted {len(rows)} work strategies")


def do_check() -> None:
    for domain in ("work", "romance"):
        n = store.strategy_count(domain)
        print(f"[{domain}] strategies: {n}")
        if not n:
            continue
        nonempty = {}
        for r in store.recall_strategies(domain, limit=1000):
            for k in ("summary_en", "guidance", "safe_script", "advanced_script",
                      "universal_lines", "bad_example", "env_notes"):
                if r.get(k):
                    nonempty[k] = nonempty.get(k, 0) + 1
        print("  field coverage:", json.dumps(nonempty, ensure_ascii=False))
        for r in store.recall_strategies(domain, limit=3):
            print(f"  - {r['scenario_code']} {r['title']} | danger {r['danger_min']}-{r['danger_max']}"
                  f" | {r['summary_en'][:90]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clone", action="store_true", help="克隆源仓库并导入中文话术")
    ap.add_argument("--llm", action="store_true", help="导入后用 DeepSeek 提炼 summary_en/guidance")
    ap.add_argument("--check", action="store_true", help="只看统计")
    ap.add_argument("--manual", metavar="FILE", help="从手填 JSON 导入")
    a = ap.parse_args()
    if a.check:
        do_check()
    elif a.manual:
        manual_import(a.manual)
    elif a.clone or a.llm:
        clone()
        do_import(llm=a.llm)


if __name__ == "__main__":
    main()
