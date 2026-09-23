# -*- coding: utf-8 -*-
"""整条链的唯一入口：对话 → 域路由 → 召回策略 → 盲起草 → Jev 增强判断+排序 →
联动重起草 → 结构化结果。

与原版的区别（原版：盲起草 → JUDGE_QUESTIONS 硬编码判断）：
1. 判断前先按域路由召回 SQLite 策略，注入 state（「Jev 查数据库」= 应用侧检索注入）
2. 联动起草：判断结果经 draft_candidates(judge=, strategies=) 反馈给起草
3. check_history 两步法：判断说要翻历史而库里历史不足时，第二跳带更早历史重判

平台无关。SSE 消费者、悬浮窗、命令行 demo 都只调 analyze()。
"""
from __future__ import annotations

import re

try:
    from . import store
    from .draft import draft_candidates
    from .jev_client import ask
    from .questions import (DOMAINS, build_rank_question, build_state,
                            questions_for)
except ImportError:
    import store
    from draft import draft_candidates
    from jev_client import ask
    from questions import (DOMAINS, build_rank_question, build_state,
                           questions_for)

_REPLY_IDX = {"reply_a": 0, "reply_b": 1, "reply_c": 2}

# relationship 字段里的职场信号（OCR 会话名/用户手填关系都会落在这串里）
_WORK_HINT = re.compile(
    r"同事|领导|老板|上司|下属|客户|甲方|领导层|经理|总监|部门|对接|项目群|工作群"
    r"|colleague|boss|manager|client|coworker|supervisor|team", re.I)


def route_domain(messages: list, relationship: str, chat_key: str | None = None,
                 default_domain: str = "romance") -> str:
    """规则路由：联系人档案 > relationship 信号 > 默认域。"""
    if chat_key:
        c = store.contact(chat_key)
        if c and c.get("domain") in DOMAINS:
            return c["domain"]
    if _WORK_HINT.search(relationship or ""):
        return "work"
    return default_domain if default_domain in DOMAINS else "romance"


def _extract_judge(answers: dict) -> dict:
    """从 Jev 答案里抽联动起草要的三元组（取不到给安全默认）。"""
    def _choice(name):
        v = (answers.get(name) or {}).get("choice")
        return str(v) if v else None

    danger = None
    try:
        danger = float((answers.get("danger_level") or {}).get("score"))
    except (TypeError, ValueError):
        pass
    return {"true_intent": _choice("true_intent") or "unclear",
            "best_action": _choice("best_action") or "acknowledge",
            "danger_level": danger}


def _injections(strategies: list, prior_context: str, contact_note: str) -> dict:
    """state 注入包：策略摘要全英文一句话；历史/档案是聊天内容，保留中文。"""
    return {
        "relevant_guidance": [s["summary_en"] for s in (strategies or [])[:4]],
        "prior_context": prior_context or "",
        "contact_note": contact_note or "",
    }


def _prior_context(chat_key: str | None, messages: list, keep: int) -> str:
    """窗口外的更早历史（本地存档）：拼成中文一段，作为 prior_context 注入。
    没开存档/库里没有就空串。"""
    if not chat_key:
        return ""
    hist = store.recent_history(chat_key, limit=60)
    if len(hist) <= len(messages[-keep:]):
        return ""
    earlier = hist[:-len(messages[-keep:])] if messages else hist
    if not earlier:
        return ""
    return "（更早的记录）" + "；".join(
        f"{m['sender']}: {m['text']}" for m in earlier[-12:])


def _pick_for_draft(strategies: list, judge: dict) -> list:
    """联动起草只带最贴的 1-2 条：危险等级落进区间优先，不够再补。"""
    try:
        d = int(round(float(judge.get("danger_level"))))
    except (TypeError, ValueError):
        d = None
    if d is None:
        return strategies[:2]
    in_band = [s for s in strategies
               if s.get("danger_min", 0) <= d <= s.get("danger_max", 9)]
    return (in_band + [s for s in strategies if s not in in_band])[:2]


def _hop2_state(messages: list, relationship: str, chat_key: str | None,
                keep: int, reply_to: str | None, domain: str,
                strategies: list) -> dict:
    """第二跳 state：当前窗口 + 库里更早历史（prior_context 拉长），换更全视野。"""
    earlier = ""
    if chat_key:
        hist = store.recent_history(chat_key, limit=60)
        if len(hist) > len(messages):
            earlier = "（更早的记录）" + "；".join(
                f"{m['sender']}: {m['text']}" for m in hist[:-len(messages)]
                if m["sender"] in ("her", "me"))[-1500:]
    return build_state(messages, relationship, keep=keep, reply_to=reply_to,
                       injections=_injections(strategies, earlier, ""))


def analyze(messages: list, relationship: str, model: str | None = None,
            timeout: float = 30, context: int = 10, provider: str = "openrouter",
            reply_to: str | None = None, style: str = "", thinking: bool = False,
            chat_key: str | None = None, default_domain: str = "romance") -> dict:
    """messages: [(from, text)] from ∈ {her, me}，最新一条在最后；
    群聊里可以带第三项 name（说这句话的人），单聊不带。
    context: 起草和判断各看最近多少条消息（用户设置里的「参考上下文」）。
    provider: 起草走哪家（openrouter / deepseek 直连）；判断和排序永远走 OpenRouter。
    reply_to: 群聊里指定回复给谁；None = 正常回复。
    style: 用户自己描述的说话风格，只影响起草。
    thinking: 起草时是否开思考模式，只影响起草，默认关。
    model=None 用该来源的默认模型。
    chat_key: 会话名（OCR 侧传标题），用于联系人档案路由 + 历史召回；None = 不用库。
    default_domain: 全局默认域（设置项），联系人没覆盖且无职场信号时用它。

    返回 {candidates, best_index, best_reply, scores, answers, usage, reply_to,
          domain, strategies, judge}。
    scores 是每条候选的胜出概率（0~1），取自 best_reply.probabilities，取不到记 0.0。
    只有对方最新说话时才有意义调它——是不是该触发由调用方判断（看 latest_from）。
    """
    # 1. 域路由 + 召回
    domain = route_domain(messages, relationship, chat_key, default_domain)
    strategies = []
    prior = ""
    note = ""
    if chat_key:
        try:
            strategies = store.recall_strategies(domain, limit=4)
            prior = _prior_context(chat_key, messages, context)
            note = (store.contact(chat_key) or {}).get("note") or ""
        except Exception:  # 库坏了不该挡正常回复
            strategies, prior, note = [], "", ""
    else:
        strategies = store.recall_strategies(domain, limit=4)

    # 2. 盲起草（排序题需要候选原文）
    candidates = draft_candidates(messages, relationship, provider=provider,
                                  model=model, timeout=timeout, keep=context,
                                  reply_to=reply_to, style=style, thinking=thinking)

    # 3. 增强判断（fan-out：COMMON + 域题 + rank 一次发）
    questions = dict(questions_for(domain))
    if len(candidates) >= 2:
        questions.update(build_rank_question(candidates))
    state = build_state(messages, relationship, keep=context, reply_to=reply_to,
                        injections=_injections(strategies, prior, note))
    result = ask(state, questions, timeout=timeout)
    answers = result.get("answers") or {}

    # 4. domain_check 仲裁：Jev 不认同路由且很确定 → 记入联系人档案，下轮生效
    judged_domain = (answers.get("domain_check") or {}).get("choice")
    if (judged_domain in DOMAINS and judged_domain != domain and chat_key):
        probs = (answers.get("domain_check") or {}).get("probabilities") or {}
        try:
            conf = float(probs.get(judged_domain, 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf >= 0.7:
            try:
                store.upsert_contact(chat_key, domain=judged_domain)
            except Exception:
                pass

    judge = _extract_judge(answers)

    # 5. check_history 两步法：判断要翻历史且库里真有更早记录 → 第二跳重判域题
    if (judge["best_action"] == "check_history" and chat_key and prior):
        h2_questions = dict(questions_for(domain))  # 无 rank，纯判断
        h2_state = _hop2_state(messages, relationship, chat_key, context,
                               reply_to, domain, strategies)
        try:
            h2 = ask(h2_state, h2_questions, timeout=timeout)
            judge = _extract_judge(h2.get("answers") or {})
            answers.update(h2.get("answers") or {})
            usage = result.get("usage") or {}
            h2u = h2.get("usage") or {}
            usage = {k: usage.get(k, 0) + h2u.get(k, 0) if isinstance(usage.get(k), (int, float)) else h2u.get(k) for k in set(usage) | set(h2u)}
            result["usage"] = usage
        except Exception:
            pass  # 第二跳失败不致命，用第一跳判断继续

    # 6. 排序解析
    best_key = (answers.get("best_reply") or {}).get("choice")
    best_index = _REPLY_IDX.get(best_key, 0)  # 解析不出就退第一条
    if best_index >= len(candidates):
        best_index = 0

    probabilities = (answers.get("best_reply") or {}).get("probabilities") or {}
    scores = [0.0, 0.0, 0.0]
    for key, idx in _REPLY_IDX.items():
        try:
            scores[idx] = float(probabilities.get(key, 0.0))
        except (TypeError, ValueError):
            scores[idx] = 0.0  # 脏数据一律按 0 处理

    # 7. 联动重起草：判断出来了且候选不止 1 条才值得重写；盲稿直接复用
    linked = candidates
    if judge.get("true_intent") != "unclear" and len(candidates) >= 2:
        try:
            linked = draft_candidates(messages, relationship, provider=provider,
                                      model=model, timeout=timeout, keep=context,
                                      reply_to=reply_to, style=style,
                                      thinking=thinking, judge=judge,
                                      strategies=_pick_for_draft(strategies, judge))
            # 重起草失败/解析烂 → 退盲稿
            if not linked:
                linked = candidates
        except Exception:
            linked = candidates

    # 重起草后的 best_index 按原排序映射不可靠（候选变了），保守取首位由 Jev 口径兜底：
    # 联动稿三条同策略不同语气，首位即稳妥版。
    # scores 是盲稿那三条的概率，跟联动稿对不上号，一并清零——overlay 见全 0 就隐藏百分比，
    # 免得出现「推荐那条标 10%、79% 的反而排在下面」这种错位。
    if linked is not candidates:
        best_index = 0
        best_key = None
        scores = [0.0] * len(linked)

    return {
        "candidates": linked,
        "best_index": best_index,
        "best_reply": linked[best_index],
        "scores": scores,
        "answers": answers,
        "usage": result.get("usage") or {},
        "reply_to": reply_to,
        "domain": domain,
        "strategies": strategies,
        "judge": judge,
    }
