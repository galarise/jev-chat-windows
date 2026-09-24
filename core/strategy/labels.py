# -*- coding: utf-8 -*-
"""域题集选项的中文标签：overlay 展示与起草小抄共用这一份。

romance 域的口径上游 core.questions.CHOICE_LABELS 已有且已过校准，不重复；
这里只给 work 域的选项（install() 时 merge 进上游表）。"""
from __future__ import annotations


def choice_labels() -> dict:
    """work 域 choice 题的中文标签，形状与上游 CHOICE_LABELS 的单题映射一致。"""
    return {
        "true_intent": {
            "assign_task": "布置任务", "blame_shift": "转移责任", "pass_work": "推活儿",
            "seek_approval": "求批准或背书", "seek_help": "求助或要信息",
            "complain_pressure": "催进度或施压", "circle_test": "站队试探",
            "casual_chat": "轻松交流", "close_topic": "平和结束话题",
        },
        "best_action": {
            "check_history": "先核对聊天记录", "take_responsibility": "担下责任给方案",
            "reject_politely": "婉拒并给替代", "ask_clarify": "先问清边界",
            "commit_plan": "给出具体承诺", "report_progress": "汇报进展",
            "escalate_explicitly": "书面/向上明确", "acknowledge": "回应并表达理解",
            "say_less": "简短回应或留白", "support_others": "为同事站台",
        },
        "she_needs": {
            "solution": "具体解决方案", "accountability": "明确担责",
            "endorsement": "支持或背书", "boundary_clarified": "边界先说清",
            "empathy": "先接住情绪", "nothing": "可能无需补充回应",
        },
    }
