# -*- coding: utf-8 -*-
"""策略库插件：域路由 + SQLite 策略召回 + 判断注入 + check_history 两跳。

核心机制：把 core.hooks.py 的 4 个默认空钩子替换为 engine_hooks 的实现。
不装这个包（不调 install()），hooks 保持空实现，行为与原版完全一致。

    from core.strategy import install
    install()  # main.py 启动时调用一次；幂等，库打不开就整体降级不装
"""
from __future__ import annotations

import traceback


def install() -> bool:
    """把插件钩子挂进 core.hooks。成功返回 True，库打不开等异常返回 False（主流程不受影响）。"""
    try:
        from core import hooks, questions as upstream_questions
        from core.strategy import engine_hooks
        from core.strategy import store  # 库真打不开的话在这里就炸，别留半挂状态

        store.init()  # 首次启动建库，建不出来走 except 整体降级

        hooks.enrich_state = engine_hooks.enrich_state
        hooks.judge_questions = engine_hooks.judge_questions
        hooks.augment_guidance = engine_hooks.augment_guidance
        hooks.after_analyze = engine_hooks.after_analyze

        # work 域的选项中文标签 merge 进上游 CHOICE_LABELS（overlay 的展示跟随上游）
        from core.strategy.questions import DOMAIN_QUESTIONS
        from core.strategy import labels as _labels
        for name, mapping in _labels.choice_labels().items():
            upstream_questions.CHOICE_LABELS.setdefault(name, {}).update(mapping)
        return True
    except Exception:
        traceback.print_exc()  # 插件坏了不该挡正常回复，但得让人看见原因
        return False
