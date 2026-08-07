#!/usr/bin/env python3
"""룰셋 조건 DSL 평가기 (Python).

`docs/app.js`의 evalCond/scoreRuleset 과 동일한 규칙으로 동작해야 한다.
한쪽을 고치면 반드시 다른 쪽도 고친다. 명세는 rules/schema.md.

판정 불가(None)와 거짓(False)을 구분하는 것이 핵심이다. 지표가 없어서 판단할 수
없는 항목을 False로 처리하면 "조건 미충족"으로 잘못 집계되어 점수가 왜곡된다.
"""
from __future__ import annotations

import json
import os
from typing import Any

RULES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "rules")

_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def load_ruleset(name: str) -> dict:
    """rules/<name>.json 로드."""
    with open(os.path.join(RULES_DIR, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def eval_cond(cond: Any, metrics: dict) -> bool | None:
    """조건식 평가. 판정 불가면 None."""
    if not isinstance(cond, dict):
        return None

    if "all" in cond:
        saw_none = False
        for c in cond["all"]:
            r = eval_cond(c, metrics)
            if r is False:
                return False
            if r is None:
                saw_none = True
        return None if saw_none else True

    if "any" in cond:
        saw_none = False
        for c in cond["any"]:
            r = eval_cond(c, metrics)
            if r is True:
                return True
            if r is None:
                saw_none = True
        return None if saw_none else False

    if "not" in cond:
        r = eval_cond(cond["not"], metrics)
        return None if r is None else (not r)

    m = cond.get("m")
    if m is None:
        return None
    left = metrics.get(m)
    if left is None:
        return None

    if "m2" in cond:
        right = metrics.get(cond["m2"])
        if right is None:
            return None
    elif "v" in cond:
        right = cond["v"]
    else:
        return None

    op = _OPS.get(cond.get("op", ""))
    if op is None:
        return None
    # bool 은 int 의 하위 타입이라 숫자 비교에 섞이면 조용히 잘못된 결과가 나온다.
    if isinstance(left, bool) != isinstance(right, bool):
        return None
    try:
        return bool(op(left, right))
    except TypeError:
        return None


def score_ruleset(ruleset: dict, metrics: dict, manual: dict | None = None) -> dict:
    """룰셋 전체를 평가해 점수·판정을 낸다.

    manual: {item_id: True/False} — 사람이 체크한 수동 항목.
    """
    manual = manual or {}
    got = 0.0
    total = 0.0
    core_miss: list[str] = []
    auto_n = 0
    items_out = []

    for g in ruleset.get("groups", []):
        for it in g.get("items", []):
            w = float(it.get("w", 0))
            total += w
            res = eval_cond(it["auto"], metrics) if "auto" in it else None
            src = "auto" if res is not None else "manual"
            if res is None:
                res = bool(manual.get(it["id"], False))
            else:
                auto_n += 1
            if res:
                got += w
            elif it.get("core"):
                core_miss.append(it["id"])
            items_out.append({"id": it["id"], "ok": res, "src": src, "w": w})

    pct = round(got / total * 100) if total else 0
    v = ruleset.get("verdict", {"go": 85, "wait": 70})
    if core_miss:
        verdict = "핵심 미충족"
    elif pct >= v.get("go", 85):
        verdict = "진입 가능"
    elif pct >= v.get("wait", 70):
        verdict = "관망"
    else:
        verdict = "제외"

    return {
        "pct": pct,
        "verdict": verdict,
        "core_miss": core_miss,
        "auto_resolved": auto_n,
        "items": items_out,
    }
