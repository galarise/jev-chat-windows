# -*- coding: utf-8 -*-
"""SQLite 数据层：策略表 + 会话历史表 + 联系人档案表。唯一 DB 入口。

stdlib sqlite3，零新依赖。key 永不进库；截图帧仍不落盘——这里存的是
OCR 出来的消息文本（用户自己聊天记录的本地存档，设置里可关）。

三个用途（对应「Jev 基于查数据库判断」）：
- recall_strategies(): 判断前按 domain + 危险等级区间召回策略，注入 state
- append_messages() / recent_history(): 采集侧入库 + check_history 真实翻记录
- contact(): 联系人档案（域别覆盖、备注），注入 state
"""
from __future__ import annotations

import os
import sqlite3
import time

try:
    from . import questions as _q  # 仅用于路径一致性；store 本身不依赖题目
except ImportError:  # 直接当脚本跑
    pass

_ROOT = (os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB_DIR = os.path.join(_ROOT, "data")
_DB = os.path.join(_DB_DIR, "jev.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
  id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL,            -- 'work' | 'romance'
  scenario_code TEXT NOT NULL,     -- 'S01' / 'icebreak'…（来源编号，可追溯）
  title TEXT NOT NULL,             -- 中文场景名（展示用）
  summary_en TEXT NOT NULL,        -- 一句话英文摘要（注入 state 的召回主力）
  guidance TEXT NOT NULL,          -- 中文打法要点（注入起草 SYSTEM）
  safe_script TEXT,                -- 稳妥型标准话术
  advanced_script TEXT,            -- 进阶型标准话术
  universal_lines TEXT,            -- 万能句式
  bad_example TEXT,                -- 错误示范
  danger_min INTEGER DEFAULT 0,    -- 适用危险等级区间下限（danger_level 0-9）
  danger_max INTEGER DEFAULT 9,    -- 上限
  env_notes TEXT,                  -- 环境差异（体制内/互联网/外企）合并文本
  source TEXT,                     -- 'gaoqingshang' / 'chat_advisor'
  UNIQUE(domain, scenario_code)
);
CREATE INDEX IF NOT EXISTS idx_strat ON strategies(domain, danger_min, danger_max);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  chat_key TEXT NOT NULL,          -- OCR 出来的会话名（worker 已有相似度归并，天然稳定 key）
  sender TEXT NOT NULL,            -- 'her' | 'me' | 群聊发言人名
  text TEXT NOT NULL,
  ts REAL NOT NULL,                -- 采集时间 epoch 秒
  UNIQUE(chat_key, ts, sender, text)   -- 幂等：OCR 重复帧不双写
);
CREATE INDEX IF NOT EXISTS idx_msg ON messages(chat_key, id);

CREATE TABLE IF NOT EXISTS contacts (
  chat_key TEXT PRIMARY KEY,       -- 会话名即主键；改名=新档案（可接受）
  domain TEXT,                     -- 该人默认域（覆盖全局路由）：'work' | 'romance' | NULL
  note TEXT,                       -- 自由档案：性格/雷点/上下级关系
  updated_ts REAL
);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    """首次启动建库。可重复调用。"""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ------------------------------------------------------------------ 策略表

def upsert_strategy(row: dict) -> None:
    """INSERT OR REPLACE 一条策略。row 的 key 与表列一致，缺的列退默认值。"""
    cols = ("domain", "scenario_code", "title", "summary_en", "guidance",
            "safe_script", "advanced_script", "universal_lines", "bad_example",
            "danger_min", "danger_max", "env_notes", "source")
    data = {c: row.get(c) for c in cols}
    if not data["domain"] or not data["scenario_code"] or not data["summary_en"]:
        raise ValueError(f"strategy missing required field: {row!r}")
    data["danger_min"] = int(data.get("danger_min") if data.get("danger_min") is not None else 0)
    data["danger_max"] = int(data.get("danger_max") if data.get("danger_max") is not None else 9)
    data["danger_min"] = max(0, min(9, data["danger_min"]))
    data["danger_max"] = max(data["danger_min"], min(9, data["danger_max"]))
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strategies(%s) VALUES (%s)" % (
                ",".join(cols), ",".join("?" * len(cols))),
            [data[c] for c in cols],
        )


def recall_strategies(domain: str, danger: float | None = None, limit: int = 4) -> list[dict]:
    """判断前召回：按 domain 全量捞出，危险等级给了就按区间过滤，超限按 scenario_code 截断。

    策略总量首轮 ≤40 条，不需要 FTS。danger None（判断前还没数）→ 只按 domain 取。"""
    q = ("SELECT domain, scenario_code, title, summary_en, guidance, safe_script,"
         " advanced_script, universal_lines, bad_example, danger_min, danger_max,"
         " env_notes, source FROM strategies WHERE domain=?")
    args: list = [domain]
    if danger is not None:
        d = int(max(0, min(9, danger)))
        q += " AND danger_min<=? AND danger_max>=?"
        args += [d, d]
    q += " ORDER BY scenario_code LIMIT ?"
    args.append(limit)
    with _connect() as conn:
        rows = [dict(zip([c[0] for c in cur.description], r))
                for cur in (conn.execute(q, args),) for r in cur.fetchall()]
    return rows


def strategy_count(domain: str | None = None) -> int:
    q = "SELECT COUNT(*) FROM strategies"
    args: list = []
    if domain:
        q += " WHERE domain=?"
        args.append(domain)
    with _connect() as conn:
        return conn.execute(q, args).fetchone()[0]


# ---------------------------------------------------------------- 会话历史表

def append_messages(chat_key: str, lines: list) -> int:
    """入库一批 OCR 消息。lines: (sender, text) / dict(sender,text)。
    sender 统一：me / her / 群发言人名。返回实际新写入条数。"""
    rows = []
    ts = time.time()
    for i, item in enumerate(lines):
        if isinstance(item, dict):
            sender, text = item.get("sender") or item.get("from"), item.get("text")
        else:
            sender, text = item[0], item[1]
        if not text or not sender:
            continue
        rows.append((str(chat_key), str(sender), str(text), round(ts, 1) + i * 0.001))
    if not rows:
        return 0
    with _connect() as conn:
        cur = conn.executemany(
            "INSERT OR IGNORE INTO messages(chat_key, sender, text, ts) VALUES (?,?,?,?)",
            rows)
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def recent_history(chat_key: str, limit: int = 60) -> list[dict]:
    """check_history 真实翻记录：按时间正序返回最近 limit 条。"""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT sender, text, ts FROM messages WHERE chat_key=?"
            " ORDER BY id DESC LIMIT ?", (chat_key, limit))
        rows = [dict(zip(["sender", "text", "ts"], r)) for r in cur.fetchall()]
    rows.reverse()
    return rows


def message_count(chat_key: str | None = None) -> int:
    q = "SELECT COUNT(*) FROM messages"
    args: list = []
    if chat_key:
        q += " WHERE chat_key=?"
        args.append(chat_key)
    with _connect() as conn:
        return conn.execute(q, args).fetchone()[0]


# ---------------------------------------------------------------- 联系人档案表

def contact(chat_key: str) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT chat_key, domain, note, updated_ts FROM contacts WHERE chat_key=?",
            (chat_key,))
        r = cur.fetchone()
    return dict(zip(["chat_key", "domain", "note", "updated_ts"], r)) if r else None


def upsert_contact(chat_key: str, domain: str | None = None,
                   note: str | None = None, merge_note: bool = False) -> None:
    """domain/note 为 None = 保留原值；merge_note=True 时 note 追加而不是覆盖。"""
    old = contact(chat_key)
    new_domain = domain if domain is not None else (old or {}).get("domain")
    if merge_note and note:
        old_note = (old or {}).get("note") or ""
        new_note = (old_note + "\n" + note).strip() if old_note and note not in old_note else (note or old_note)
    else:
        new_note = note if note is not None else (old or {}).get("note")
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO contacts(chat_key, domain, note, updated_ts)"
            " VALUES (?,?,?,?)",
            (chat_key, new_domain, new_note, time.time()))


if __name__ == "__main__":
    # 自测：临时库跑一遍读写
    import tempfile, sys
    tmp = tempfile.mkdtemp()
    _DB_DIR, _DB = os.path.join(tmp, "data"), os.path.join(tmp, "data", "t.db")
    init()
    upsert_strategy({"domain": "work", "scenario_code": "S01", "title": "领导批评",
                     "summary_en": "S01: Supervisor criticizes — own the fix, no excuses.",
                     "guidance": "先认态度，再客观陈述原因，最后给改进方案。"})
    assert strategy_count("work") == 1
    got = recall_strategies("work", danger=7)
    assert got and got[0]["scenario_code"] == "S01"
    assert recall_strategies("work", danger=0) != []  # 未写区间的策略默认 0-9 全适用
    upsert_strategy({"domain": "work", "scenario_code": "S09", "title": "同事甩锅",
                     "summary_en": "S09: Peer shifts work; clarify ownership, offer handoff.",
                     "guidance": "不当场翻脸，先摆事实，再走正式分工。",
                     "danger_min": 3, "danger_max": 7})
    assert recall_strategies("work", danger=0) and \
        all(r["scenario_code"] != "S09" for r in recall_strategies("work", danger=0))
    assert any(r["scenario_code"] == "S09" for r in recall_strategies("work", danger=5))
    n = append_messages("测试群", [{"sender": "her", "text": "在吗"}, ("me", "来了")])
    assert n == 2, n
    # 幂等：ts 按 0.1s 粒度取整，同一秒内完全相同的一批重放会被 INSERT OR IGNORE 挡掉；
    # 真正隔了几秒的同文本消息是两条（合法：同一句话隔十分钟再发确实发了两次）。
    n_replay = append_messages("测试群", [{"sender": "her", "text": "在吗"}, ("me", "来了")])
    assert n_replay == 0, n_replay  # 同秒同批重放：ts 粒度对齐，全部挡掉
    time.sleep(1.1)
    assert append_messages("测试群", [("her", "在吗")]) == 1  # 隔秒同文本 = 新消息，正常入
    assert message_count("测试群") == 3
    assert len(recent_history("测试群")) == 3
    assert recent_history("测试群")[0]["sender"] == "her"  # 正序
    assert recent_history("测试群")[-1]["sender"] == "her"  # 最新那条也是隔秒重发的 her
    upsert_contact("测试群", domain="work")
    upsert_contact("测试群", note="部门经理，怕当众批评", merge_note=True)
    c = contact("测试群")
    assert c["domain"] == "work" and "部门经理" in c["note"]
    print("store ok")
