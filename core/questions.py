"""Jev 题集分层：COMMON（全域共用）+ DOMAIN_QUESTIONS（职场/恋爱两域）。

硬约束（沿用上游已验证口径）：
- instructions/criteria 全英文（Jev 主训练语言英文）；state 里的聊天内容保留中文
- score criteria 有序数组 2-10 档，每档写具体情景不写抽象程度
- choice criteria ≤255 项
- 一次请求发全部题（speculative fan-out）

分层结构：
- COMMON：literal_question / domain_check / danger_level / should_reply_now /
  tension_resolved —— 跟域无关的通用判断
- DOMAIN_QUESTIONS[domain]：true_intent / best_action / she_needs —— 选项集按域写
  - work：任务/甩锅/审批/施压/站队试探 等职场意图
  - romance：原版七题的恋爱口径逐字保留（那套已过校准，别动）

新增 build_state(injections=...)：把 DB 召回的策略摘要/历史概括/联系人档案
作为 context_notes 注入 state（英文、每项一句话）。这是「Jev 查数据库判断」
的实现方式：应用侧检索 → 注入 → Jev 对增强 state 做判断。
"""

from __future__ import annotations

DOMAINS = ("work", "romance")

# ---------------------------------------------------------------- 共用题

COMMON: dict = {
    "literal_question": {
        "type": "noul",
        "instructions": (
            "Is the other person's latest message meant purely literally, with no subtext? "
            "Judge from the whole thread, not one sentence in isolation."
        ),
        "criteria": {
            "true": (
                "The latest message is a straightforward statement, question, or plan "
                "with no implied accusation, test, sarcasm, hint, or unsaid request."
            ),
            "false": (
                "There is subtext: a test of whether you remember or care, sarcasm, "
                "an implied complaint, a hint they will not say outright, a trap question, "
                "an accusation dressed as a question, or a cold/short line that really means blame."
            ),
        },
    },
    "domain_check": {
        "type": "choice",
        "instructions": (
            "Which domain does this conversation belong to? Judge from the whole thread "
            "and the relationship field. Personal-feelings talk between partners, family "
            "or friends is interpersonal_emotional even if it mentions errands or plans. "
            "Task, deadline, deliverable, approval or accountability talk between "
            "colleagues, a boss, a client or a teammate is work_or_task. "
            "If a personal relationship is discussing a shared task (couple planning a "
            "move), choose mixed."
        ),
        "criteria": {
            "interpersonal_emotional": (
                "Feelings, loyalty, care, affection, jealousy, apology or relationship "
                "status are the main thread. Work words may appear but the stakes are "
                "emotional."
            ),
            "work_or_task": (
                "Deliverables, deadlines, assignments, blame for outcomes, approvals, "
                "progress reports or workplace hierarchy drive the exchange; personal "
                "feelings are background at most."
            ),
            "mixed": (
                "A personal relationship is coordinating a concrete shared task, or a "
                "workplace thread has turned personal. Both domains carry real weight."
            ),
        },
    },
    "danger_level": {
        "type": "score",
        "instructions": (
            "How close is this conversation to a fight or to damaging the relationship? "
            "Match the current scene. "
            "If they genuinely accepted an apology or confirmed a happy plan, score the "
            "cooled-down present, not an earlier complaint. "
            "If an ultimatum (ending it, reporting to the boss, escalating formally) is "
            "still in force and has not been withdrawn, stay in that high bin even if the "
            "latest line names a specific task."
        ),
        "criteria": [
            "Light chat or joking; no complaint, no test, no deadline.",
            "Mild tease or a small reminder that is easy to laugh off; a clumsy reply "
            "would only feel slightly awkward.",
            "A mild complaint or 'please remember next time' said without heat; they "
            "still send warm or practical follow-ups.",
            "Noticeable unhappiness; they mention being let down, ignored, or kept "
            "waiting, but still give you a chance to make it right.",
            "Sarcasm, cold short replies, or 'you better'; they are testing you, and a "
            "sloppy or fake-confident reply will escalate.",
            "Openly upset; they accuse you of not listening or not caring; they expect a "
            "real response, not a joke.",
            "Clearly angry and blaming you; a wrong reply will turn this into a fight.",
            "Last-chance warning. They will not cover for you, do not want to keep "
            "talking unless this changes, or trust is almost gone.",
            "An ultimatum is already on the table even if they also give a practical "
            "next step: end it if you forget again, escalate tonight, or stop covering "
            "for you if you miss this.",
            "Active rupture: they said it is over, told you not to reply, removed you, "
            "or are exploding.",
        ],
    },
    "should_reply_now": {
        "type": "noul",
        "instructions": (
            "Should your next message contain substantive content? "
            "Substantive means: admitting a specific known fault, giving a concrete "
            "time/plan/deliverable, explaining facts you actually know, or reciting the "
            "recalled content they asked you to say. "
            "This is NOT 'should you send any message'. Timing is irrelevant. "
            "Answer FALSE if the thing they want you to recite or prove is not present "
            "in this snippet (you would be guessing). 'Then say it' / 'you better' while "
            "you are stalling is FALSE. "
            "Answer FALSE if they already accepted and closed the topic. "
            "Answer true only if the needed fact, plan, or named fault is already in "
            "this snippet or in the prior_context note."
        ),
        "criteria": {
            "true": (
                "The needed fact, named fault, or named time/place is already in this "
                "snippet or in prior_context, and they are waiting for that substance now."
            ),
            "false": (
                "Do not put substance in the next message: the recalled content is not "
                "available yet, they are testing whether you remember, a holding line is "
                "enough, saying less is safer, or they already closed the topic."
            ),
        },
    },
    "tension_resolved": {
        "type": "noul",
        "instructions": (
            "Has interpersonal tension already been resolved? "
            "Answer true only if there was never tension, or the other person has "
            "clearly accepted, cooled down, joked again, or said it is fine. "
            "A sarcastic 'you better', an unanswered test, leftover blame, or an open "
            "ultimatum means false."
        ),
        "criteria": {
            "true": (
                "No remaining tension: they accepted, joked again, said it's fine, "
                "confirmed a happy plan, or the chat was never tense."
            ),
            "false": (
                "Tension is still present: they are waiting, testing, angry, sarcastic, "
                "issuing an ultimatum, or the issue is open."
            ),
        },
    },
}

# ---------------------------------------------------------------- 职场题集

_WORK: dict = {
    "true_intent": {
        "type": "choice",
        "instructions": (
            "What is the other person's true intent in the latest message, given the "
            "full conversation? Prefer tone and context over surface wording. "
            "If a request would move work onto you that is not yours, choose "
            "blame_shift or pass_work even if the words are polite. "
            "If they already accepted and closed the matter peacefully, choose "
            "close_topic."
        ),
        "criteria": {
            "assign_task": (
                "They are giving you a task, deadline, or deliverable, and it is within "
                "your role or already agreed."
            ),
            "blame_shift": (
                "They are moving responsibility for a problem onto you, in a chat or "
                "toward a record, for something whose ownership is unclear or theirs."
            ),
            "pass_work": (
                "They are trying to hand you work that belongs to them or to someone "
                "else, politely or with pressure, but no blame is landing yet."
            ),
            "seek_approval": (
                "They want your sign-off, opinion, or endorsement before they proceed."
            ),
            "seek_help": (
                "They are genuinely asking for help, information, or support and the "
                "request is legitimate."
            ),
            "complain_pressure": (
                "They are pushing for progress, expressing dissatisfaction with pace or "
                "quality, or applying pressure, without assigning blame to you personally."
            ),
            "circle_test": (
                "They are probing where you stand: asking you to take sides, comment on "
                "someone, or commit publicly in a meeting."
            ),
            "casual_chat": (
                "Light talk, small talk, or friendly logistics with no ask and no "
                "conflict."
            ),
            "close_topic": (
                "Peaceful wrap-up only: they accepted your explanation or plan, thanked "
                "you, or clearly signaled nothing more is needed."
            ),
        },
    },
    "best_action": {
        "type": "choice",
        "instructions": (
            "What type of next action is best? Do not decide whether to send a message "
            "immediately. Ignore timing. Choose only the action type. "
            "If they asked you to recall a specific past message or commitment and you "
            "have not shown that you actually remember it, choose check_history — do "
            "not apologize or invent a plan instead. "
            "If ownership of the problem is unclear, prefer ask_clarify over accepting "
            "blame."
        ),
        "criteria": {
            "check_history": (
                "Look up prior chat, records, or written decisions before taking a "
                "position. Use when they cite a commitment or a past agreement."
            ),
            "take_responsibility": (
                "Own your part of a real failure plainly, state what already went well "
                "or what you control, and give the fix — without accepting blame that "
                "is not yours."
            ),
            "reject_politely": (
                "Decline a request while keeping the relationship: state the constraint "
                "objectively and offer an alternative or a proper channel."
            ),
            "ask_clarify": (
                "Ask for scope, boundary, or criteria before taking a position — when "
                "the ask is vague, the ownership unclear, or the terms unstated."
            ),
            "commit_plan": (
                "Give a concrete promise, deadline, or arrangement they asked for."
            ),
            "report_progress": (
                "Give a factual status: done, in progress, blocked, with numbers and a "
                "next step."
            ),
            "escalate_explicitly": (
                "Put the decision or the record upward or in writing: request a "
                "decision from the boss, confirm scope by email, or state the "
                "trade-off so the choice is no longer silently yours."
            ),
            "acknowledge": (
                "Show you heard them, without new facts, promises, or a plan. Use when "
                "they mainly need to feel heard before any substance."
            ),
            "say_less": (
                "Keep it short or add nothing. Extra words would over-explain, reopen a "
                "closed matter, or feed an ultimatum that told you not to talk."
            ),
            "support_others": (
                "Stand with or back up a colleague in the thread, or credit someone by "
                "name, without attacking anyone."
            ),
        },
    },
    "she_needs": {
        "type": "choice",
        "instructions": (
            "What does the other person need from you right now? Judge the LATEST "
            "message first. "
            "If they genuinely accepted (thanks / got it / will do / 那就这样 / 收到了), "
            "you MUST choose nothing, even if earlier they wanted action or an answer. "
            "Sarcastic 'we'll see', 'whatever', or a public jab is NOT satisfaction — "
            "do not choose nothing. "
            "If they asked you to recap a named time/place/commitment, choose action."
        ),
        "criteria": {
            "solution": (
                "They need a concrete fix, plan, deliverable, or follow-through, and "
                "have not received it yet."
            ),
            "accountability": (
                "They need you to own a failure clearly and say what changes — not an "
                "explanation that dodges ownership."
            ),
            "endorsement": (
                "They need your agreement, backing, or a public show of support."
            ),
            "boundary_clarified": (
                "They need the scope, ownership, or terms spelled out before anything "
                "moves — a yes would overcommit and a no would be premature."
            ),
            "empathy": (
                "They need the pressure or frustration acknowledged, not solved this "
                "turn."
            ),
            "nothing": (
                "They need nothing further. Genuine acceptance, a peaceful closed "
                "topic, or warm small talk with no ask. Not sarcasm pretending to be "
                "fine."
            ),
        },
    },
}

# ---------------------------------------------------------------- 恋爱题集（原版口径逐字保留，已过校准）

_ROMANCE: dict = {
    "true_intent": {
        "type": "choice",
        "instructions": (
            "What is the other person's true intent in the latest message, given the "
            "full conversation? "
            "Prefer tone and context over surface wording. "
            "If they are checking whether you remember something or still care, choose "
            "confirm_you_care even if the words look like a request to 'say it' or to "
            "do something. "
            "If they already accepted and closed the matter peacefully, choose "
            "close_topic. "
            "Ending the relationship, deleting you, or 'don't talk to me' is "
            "vent_anger, never close_topic."
        ),
        "criteria": {
            "confirm_you_care": (
                "They are testing whether you remember, pay attention, or still care. "
                "Signals: 'did you forget again', 'then say it', 'you better', sarcastic "
                "'busy person', asking you to prove you know a past conversation. "
                "If they mainly want a new deliverable or a yes on a time, do not use "
                "this."
            ),
            "vent_anger": (
                "They are angry or hurt and mainly want the feeling acknowledged. "
                "They are blaming or raising the temperature; a specific plan is not the "
                "main point yet."
            ),
            "request_action": (
                "They want a concrete action, time, deliverable, or commitment from you "
                "now, and this is a real ask, not a loyalty test."
            ),
            "seek_explanation": (
                "They want a factual explanation of why something happened. "
                "They asked why or what is going on, not mainly for an apology or a new "
                "plan."
            ),
            "casual_chat": (
                "Light talk, banter, sharing, teasing with a laugh, or friendly "
                "logistics with no emotional test and no conflict. A friend suggesting a "
                "meal time can be this if the thread is warm."
            ),
            "close_topic": (
                "Peaceful wrap-up only: they accepted an apology, confirmed a happy "
                "plan, said thanks, or clearly signaled they need nothing more. "
                "Not a breakup, not 'don't contact me', not sarcastic 'I'm used to it'."
            ),
        },
    },
    "best_action": {
        "type": "choice",
        "instructions": (
            "What type of next action is best? Do not decide whether to send a message "
            "immediately. Ignore timing. Choose only the action type. "
            "If they asked you to recall a specific past message or event and you have "
            "not shown that you actually remember it, choose check_history — do not "
            "apologize or invent a plan instead."
        ),
        "criteria": {
            "check_history": (
                "Look up prior chat or facts before taking a position. "
                "Use when they ask you to repeat, recall, or prove you remember "
                "something specific."
            ),
            "apologize": (
                "Lead with a sincere apology for a real mistake or hurt already "
                "identified. Not for an unnamed forgotten thing when you should first "
                "find out what it was."
            ),
            "give_commitment": (
                "Give a concrete promise, deadline, or arrangement they asked for in a "
                "conflict or work-pressure setting."
            ),
            "explain": (
                "Explain what happened or why, without leading with apology or a new "
                "plan."
            ),
            "acknowledge": (
                "Show you heard them and care, without new facts, an apology, or a "
                "plan. Use for light chat or when they mainly need to feel seen."
            ),
            "say_less": (
                "Keep it short or add nothing. Extra words would over-explain, reopen a "
                "closed topic, or pour fuel on an ultimatum that told you not to talk."
            ),
            "make_plan": (
                "Propose or confirm logistics (time, place, task) for a non-conflict "
                "request such as a meal or a meeting."
            ),
        },
    },
    "she_needs": {
        "type": "choice",
        "instructions": (
            "What does the other person need from you right now? Judge the LATEST "
            "message first. "
            "If they genuinely accepted (thanks / got it / 没事了 / 那就这样 / 收到了 / "
            "过去了), you MUST choose nothing, even if earlier they wanted action or an "
            "apology. "
            "Sarcastic 'I'm used to it', 'whatever', 'I don't want to hear it', 'don't "
            "bother coming' is NOT genuine satisfaction — do not choose nothing. "
            "If they asked you to recap a named time/place/date, choose action. "
            "If they are testing whether you remember or still care, and the content is "
            "unnamed, choose care."
        ),
        "criteria": {
            "apology": (
                "They need a sincere apology for hurt or a mistake, and they have not "
                "accepted one yet."
            ),
            "action": (
                "They need a concrete action, time, commitment, recap of a named fact, "
                "or follow-through, and they have not yet accepted one."
            ),
            "explanation": (
                "They need a clear explanation of what happened or why, and have not "
                "received it."
            ),
            "care": (
                "They need proof you remember, listen, or care — a loyalty or attention "
                "test — not yet a plan or an apology. Sarcastic 'I am used to it' "
                "belongs here, not nothing."
            ),
            "nothing": (
                "They need nothing further. Genuine acceptance, a peaceful closed "
                "topic, warm casual chat with no ask, or a rupture where they told you "
                "not to reply. Not sarcasm pretending to be fine."
            ),
        },
    },
}

DOMAIN_QUESTIONS: dict = {"work": _WORK, "romance": _ROMANCE}


def questions_for(domain: str) -> dict:
    """一次请求的完整题集 = COMMON + 域题集。fan-out 保持。"""
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain {domain!r}; expected one of {DOMAINS}")
    return dict(COMMON) | DOMAIN_QUESTIONS[domain]


# 兼容旧引用（demo 脚本等）：等价于 romance 域题集
JUDGE_QUESTIONS: dict = COMMON | _ROMANCE


# ---------------------------------------------------------------- state 构造

def build_state(messages: list, relationship: str, keep: int = 10,
                reply_to: str | None = None, injections: dict | None = None) -> dict:
    """messages: (from, text) / (from, text, name) / dict（name 可选）。from 只认 her/me。

    name = 群里的发言人；有 name 就当群聊（chat.is_group）。reply_to = 群里指定的回复对象。
    injections: store.recall_* 产出的注入项，各字段全英文、一句话：
      {"relevant_guidance": ["S04: ...", ...] ≤4条, "prior_context": "2-3句英文概括",
       "contact_note": "英文一句话"} → 挂在 chat.context_notes，与 messages 同级。
    """
    cleaned = []
    for item in messages:
        if isinstance(item, dict):
            who, text, name = item.get("from"), item.get("text"), item.get("name")
        else:
            who, text = item[0], item[1]
            name = item[2] if len(item) > 2 else None
        if who not in ("her", "me"):
            raise ValueError(f"message from must be 'her' or 'me', got {who!r}")
        message = {"from": who, "text": str(text)}
        if name:
            message["name"] = str(name)
        cleaned.append(message)
    cleaned = cleaned[-keep:]
    latest_from = cleaned[-1]["from"] if cleaned else "her"
    chat = {
        "relationship": relationship,
        "messages": cleaned,
        "latest_from": latest_from,
        "is_group": any("name" in m for m in cleaned),
    }
    if reply_to:
        chat["reply_to"] = str(reply_to)
    if injections:
        chat["context_notes"] = {
            k: v for k, v in injections.items() if v not in (None, [], "")
        }
    return {"chat": chat}


def build_rank_question(candidates: list[str]) -> dict:
    """Build the best_reply choice question. criteria values stay in original Chinese."""
    if not 2 <= len(candidates) <= 3:
        raise ValueError("build_rank_question expects 2 or 3 candidate replies")
    keys = ("reply_a", "reply_b", "reply_c")[:len(candidates)]
    return {
        "best_reply": {
            "type": "choice",
            "instructions": (
                "Which candidate reply is the most appropriate next message, "
                "given the conversation and the other person's true need? "
                "Prefer a reply that matches the best action type and the injected "
                "guidance. "
                "Penalize dismissive, over-promising, or off-topic replies. "
                "If the facts are not yet confirmed, prefer the candidate that looks "
                "them up instead of faking memory or a vague apology."
            ),
            "criteria": {key: text for key, text in zip(keys, candidates)},
        }
    }
