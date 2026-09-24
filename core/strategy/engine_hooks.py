# -*- coding: utf-8 -*-
"""core.hooks 四个钩子的策略库实现。

管线（配合上游三段式，不多一次起草调用）：
1. enrich_state：判断前域路由 → 召回策略/更早历史/联系人档案 → 注入 state
2. judge_questions：换域题集（COMMON + work/romance 域题）
3. augment_guidance：起草小抄追加召回策略的中文打法段
4. after_analyze：domain_check 仲裁写档 + check_history 第二跳重判 + 结果补域/策略字段
"""
from __future__ import annotations

import re

try:  # 当模块导入 / 当脚本直接跑 都能用
    from . import store
    from .questions import DOMAINS, build_state, questions_for
except ImportError:  # 直接当脚本跑
    import store
    from questions import DOMAINS, build_state, questions_for

# relationship 字段里的职场信号（OCR 会话名/用户手填关系都会落在这串里）
_WORK_HINT = re.compile(
    r"同事|领导|老板|上司|下属|客户|甲方|经理|总监|部门|对接|项目群|工作群"
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
    from app import settings  # 延迟导入：core 不依赖 app，插件装不上设置就读默认

    default_domain = getattr(settings, "default_domain", lambda: "romance")()
    return default_domain if default_domain in DOMAINS else "romance"


def _prior_context(chat_key: str, messages: list, keep: int) -> str:
    """窗口外的更早历史（本地存档）：拼成中文一段，作为 prior_context 注入。

    没开存档/库里没有就空串（build_state 会过滤空值）。"""
    hist = store.recent_history(chat_key, limit=60)
    window = messages[-keep:] if messages else []
    if len(hist) <= len(window):
        return ""
    earlier = hist[:-len(window)] if window else hist
    if not earlier:
        return ""
    return "（更早的记录）" + "；".join(
        f"{m['sender']}: {m['text']}" for m in earlier[-12:])


def _injections(strategies: list, prior_context: str, contact_note: str) -> dict:
    """state 注入包：策略摘要全英文一句话；历史/档案是聊天内容，保留中文。"""
    return {
        "relevant_guidance": [s["summary_en"] for s in (strategies or [])[:4]],
        "prior_context": prior_context or "",
        "contact_note": contact_note or "",
    }


# ---------------------------------------------------------------- 四个钩子

def enrich_state(state, messages, relationship, chat_key, context=10):
    """判断前增强 state：域路由 + 策略/历史/档案召回注入。库不可用就原样返回。

    顺手把召回结果存到模块级 _strategies，augment_guidance（起草时）直接取用——
    起草发生在 after_analyze 之前，不能靠它设置。"""
    try:
        domain = route_domain(messages, relationship, chat_key)
        strategies = store.recall_strategies(domain, limit=4)
        enrich_state._strategies = strategies  # 起草小抄用
        enrich_state._domain = domain          # after_analyze 直接取，不再重复路由
        prior = _prior_context(chat_key, messages, context) if chat_key else ""
        note = (store.contact(chat_key) or {}).get("note") if chat_key else ""
        if not strategies and not prior and not note:
            return state
        return build_state(messages, relationship, keep=context,
                           reply_to=state.get("chat", {}).get("reply_to"),
                           injections=_injections(strategies, prior, note))
    except Exception:
        return state  # 库坏了不该挡正常回复


def judge_questions():
    """域题集。首轮 ask 在 enrich_state 之后，此时域已路由好，直接给域题集。

    enrich_state 没跑过（异常/库空）→ 返回 None，engine 用上游默认题集。"""
    domain = getattr(enrich_state, "_domain", None)
    if domain is None:
        return None
    try:
        return questions_for(domain)
    except Exception:
        return None


def augment_guidance(text, answers):
    """起草小抄追加召回策略的中文打法段。无召回返回原文。"""
    strategies = getattr(enrich_state, "_strategies", None)
    if not strategies:
        return text
    blocks = []
    for s in (strategies or [])[:2]:
        blocks.append(
            f"参考打法（只取神不照抄，要用 me 自己的口吻说出来；场景：{s['title']}）：\n"
            f"- 要点：{s['guidance']}\n"
            f"- 稳妥版参考：{s.get('safe_script') or '（无）'}\n"
            f"- 进阶版参考：{s.get('advanced_script') or '（无）'}")
    added = "\n\n参考打法（判断模型已给出方向，三条候选都落在这个策略上，区别只在语气和长短）：" \
            + "\n\n".join(blocks)
    return (text or "") + added


def after_analyze(result, *, state=None, answers=None, judged=False, messages=None,
                  relationship="", context=10, timeout=20, chat_key=None,
                  jev_provider="openrouter", jev_model=None):
    """① domain_check 仲裁写档；② check_history 第二跳重判；③ 结果补域/策略字段。"""
    try:
        from core.jev_client import JevError, ask
    except ImportError:  # 直接当脚本跑
        from jev_client import JevError, ask

    domain = getattr(enrich_state, "_domain", None)
    if domain is None:
        domain = route_domain(messages, relationship, chat_key)
    strategies = getattr(enrich_state, "_strategies", None)
    if strategies is None:
        strategies = store.recall_strategies(domain, limit=4)
    prior = _prior_context(chat_key, messages, context) if chat_key else ""

    # ① domain_check 仲裁：Jev 不认同规则路由且很确定 → 记入联系人档案，下轮生效
    judged_domain_map = {"interpersonal_emotional": "romance", "work_or_task": "work"}
    judged_domain = judged_domain_map.get((answers or {}).get("domain_check", {}).get("choice"))
    if judged_domain and judged_domain != domain and chat_key:
        probs = ((answers or {}).get("domain_check") or {}).get("probabilities") or {}
        try:
            conf = float(probs.get((answers or {}).get("domain_check", {}).get("choice"), 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf >= 0.7:
            try:
                store.upsert_contact(chat_key, domain=judged_domain)
            except Exception:
                pass

    # ② check_history 两步法：判断要翻历史且库里真有更早记录 → 第二跳带更早历史重判域题
    if ((answers or {}).get("best_action", {}).get("choice") == "check_history"
            and chat_key and prior and judged):
        try:
            h2_state = build_state(messages, relationship, keep=context,
                                   reply_to=state.get("chat", {}).get("reply_to") if state else None,
                                   injections=_injections(
                                       strategies, prior,
                                       (store.contact(chat_key) or {}).get("note") or ""))
            h2 = ask(h2_state, questions_for(domain), timeout=timeout,
                     provider=jev_provider, model=jev_model)
            h2_answers = h2.get("answers") or {}
            # 第二跳没有 rank 题，别把第一跳的排序答案冲掉
            for k, v in h2_answers.items():
                if k != "best_reply":
                    answers[k] = v
            usage = result.get("usage") or {}
            for k, v in (h2.get("usage") or {}).items():
                usage[k] = usage.get(k, 0) + v if isinstance(usage.get(k), (int, float)) and isinstance(v, (int, float)) else v
        except JevError:
            pass  # 第二跳失败不致命，用第一跳判断继续

    # ③ 结果补域/策略字段（overlay 展示策略来源用；没召回是空列表，界面自动隐藏）
    result["domain"] = domain
    result["strategies"] = strategies
    return result
