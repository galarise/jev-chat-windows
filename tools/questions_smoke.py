# -*- coding: utf-8 -*-
"""题集结构本地冒烟：不联网。断言分层结构、语言、条数约束、域不串。"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.strategy.questions import (COMMON, DOMAIN_QUESTIONS, DOMAINS,
                                     JUDGE_QUESTIONS, build_rank_question,
                                     build_state, questions_for)


_ALLOWED_CJK = {"没事了", "那就这样", "收到了", "过去了", "你最好了"}  # 原版口径里的中文示例短语，逐字保留


def _assert_en(obj, where):
    """instructions 与 criteria 的文案必须全英文（原版恋爱题集 instructions 里带
    「没事了/那就这样」等中文示例短语，那是口径的一部分，白名单放行）。"""
    if isinstance(obj, str):
        long_cjk = [c for c in re.findall(r"[\u4e00-\u9fff]{3,}", obj)
                    if c not in _ALLOWED_CJK]
        assert not long_cjk, f"{where} has long Chinese text: {long_cjk[:3]}"
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _assert_en(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_en(v, f"{where}[{i}]")


def run() -> None:
    # 1. 分层结构
    assert set(DOMAINS) == {"work", "romance"}
    common_keys = {"literal_question", "domain_check", "danger_level",
                   "should_reply_now", "tension_resolved"}
    assert set(COMMON) == common_keys, set(COMMON)
    for d in DOMAINS:
        q = questions_for(d)
        assert set(q) == common_keys | {"true_intent", "best_action", "she_needs"}, (d, set(q))
        assert q["danger_level"]["type"] == "score"
        assert 2 <= len(q["danger_level"]["criteria"]) <= 10
        assert q["danger_level"]["criteria"][0] != q["danger_level"]["criteria"][-1]

    # 2. 域不串：恋爱专属选项绝不出现在职场题集
    for romance_only in ("confirm_you_care", "vent_anger", "apologize", "care"):
        assert romance_only not in DOMAIN_QUESTIONS["work"]["true_intent"]["criteria"]
        assert romance_only not in DOMAIN_QUESTIONS["work"]["best_action"]["criteria"]
    for work_only in ("blame_shift", "escalate_explicitly", "take_responsibility"):
        assert work_only not in DOMAIN_QUESTIONS["romance"]["true_intent"]["criteria"]
        assert work_only not in DOMAIN_QUESTIONS["romance"]["best_action"]["criteria"]

    # 3. criteria 条数与语言
    for d in DOMAINS:
        for name, q in questions_for(d).items():
            assert q["type"] in ("noul", "choice", "score"), (d, name)
            if q["type"] == "choice":
                assert len(q["criteria"]) <= 255, (d, name)
                _assert_en(q["criteria"], f"{d}.{name}.criteria")
            _assert_en(q["instructions"], f"{d}.{name}.instructions")

    # 4. build_state 注入
    msgs = [("her", "这事你负责的部分怎么还没好"), ("me", "我看下")]
    s1 = build_state(msgs, "colleagues")
    assert "context_notes" not in s1["chat"]
    inj = {"relevant_guidance": ["S04: Supervisor shifts blame — clarify scope first.",
                                 "S09: Peer shifts work; clarify ownership."],
           "prior_context": "They delivered the spec yesterday and asked for review by Friday.",
           "contact_note": ""}
    s2 = build_state(msgs, "colleagues", injections=inj)
    cn = s2["chat"]["context_notes"]
    assert len(cn["relevant_guidance"]) == 2
    assert "contact_note" not in cn  # 空字符串被过滤
    assert s2["chat"]["messages"][0]["text"] == msgs[0][1]  # 中文原文保留

    # 5. build_rank_question 兼容
    rq = build_rank_question(["好", "收到", "行"])
    assert set(rq) == {"best_reply"}
    assert set(rq["best_reply"]["criteria"]) == {"reply_a", "reply_b", "reply_c"}

    # 6. JUDGE_QUESTIONS 兼容层 = romance 全量
    assert set(JUDGE_QUESTIONS) == set(questions_for("romance")) | {"domain_check"}

    print("questions_smoke ok")


if __name__ == "__main__":
    run()
