#!/usr/bin/env python3
"""백테스트 결과 분석 — 성과 통계, 항목별 판별력, 가중치 제안, 파라미터 스윕.

가중치 제안의 논리
  현재 룰셋의 w 값은 근거 없는 추정치다. 이를 실측으로 바꾸려면 각 항목이
  '충족일 때와 아닐 때의 결과 차이(lift)'를 만들어내는지 봐야 한다.
  그러려면 충족·미충족 양쪽 표본이 필요하므로, 연구 모드로 룰셋 통과 여부와
  무관하게 진입해 수집한 표본을 쓴다.

과최적화 방어
  학습기간에서 뽑은 가중치를 검증기간에 그대로 적용해 성과가 얼마나 유지되는지 본다.
  유지율이 낮으면 그 가중치는 과거에만 맞는 숫자다.
"""
from __future__ import annotations

import math
from dataclasses import asdict

from engine import Params, Trade


# ── 성과 통계 ─────────────────────────────────────────────────────────
def stats(trades: list[Trade]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    rs = [t.r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    wr = len(wins) / n
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = sum(losses) / len(losses) if losses else 0.0
    expectancy = wr * avg_w + (1 - wr) * avg_l

    # 자본곡선은 R 단위 누적 — 계좌 크기 가정에 결과가 좌우되지 않는다
    eq, peak, mdd, run_loss, max_run = 0.0, 0.0, 0.0, 0, 0
    for t in trades:
        eq += t.r
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        run_loss = run_loss + 1 if t.r <= 0 else 0
        max_run = max(max_run, run_loss)

    gross_w = sum(wins)
    gross_l = -sum(losses)
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1

    return {
        "n": n,
        "win_rate": round(wr * 100, 1),
        "avg_win_r": round(avg_w, 3),
        "avg_loss_r": round(avg_l, 3),
        "expectancy_r": round(expectancy, 3),
        "total_r": round(sum(rs), 1),
        "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else None,
        "max_drawdown_r": round(mdd, 1),
        "max_consec_losses": max_run,
        "avg_bars": round(sum(t.bars for t in trades) / n, 1),
        "exit_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])),
    }


def split_by_date(trades: list[Trade], cutoff: str) -> tuple[list[Trade], list[Trade]]:
    """진입일 기준 분할. 학습기간에 진입한 매매가 검증기간으로 새지 않게 한다."""
    train = [t for t in trades if t.entry_date < cutoff]
    test = [t for t in trades if t.entry_date >= cutoff]
    return train, test


# ── 항목별 판별력 ─────────────────────────────────────────────────────
def item_lift(trades: list[Trade], min_n: int = 20) -> list[dict]:
    """각 룰 항목이 충족일 때와 아닐 때의 평균 R 차이."""
    ids: set[str] = set()
    for t in trades:
        ids |= set(t.items.keys())

    out = []
    for iid in sorted(ids):
        yes = [t.r for t in trades if t.items.get(iid) is True]
        no = [t.r for t in trades if t.items.get(iid) is False]
        if len(yes) < min_n or len(no) < min_n:
            out.append({"id": iid, "n_yes": len(yes), "n_no": len(no),
                        "lift": None, "note": "표본 부족"})
            continue
        my, mn = sum(yes) / len(yes), sum(no) / len(no)
        vy = sum((x - my) ** 2 for x in yes) / max(1, len(yes) - 1)
        vn = sum((x - mn) ** 2 for x in no) / max(1, len(no) - 1)
        se = math.sqrt(vy / len(yes) + vn / len(no))
        t_stat = (my - mn) / se if se > 0 else 0.0
        out.append({
            "id": iid, "n_yes": len(yes), "n_no": len(no),
            "mean_r_yes": round(my, 3), "mean_r_no": round(mn, 3),
            "lift": round(my - mn, 3), "t": round(t_stat, 2),
            # |t| < 2 는 우연과 구분되지 않는다 — 가중치 근거로 쓰지 않는다
            "significant": abs(t_stat) >= 2.0,
        })
    out.sort(key=lambda x: -(x["lift"] or -99))
    return out


def core_blockers(signals: list[dict], ruleset: dict) -> list[dict]:
    """어떤 core 항목이 신호를 가장 많이 탈락시키는가.

    룰셋이 지나치게 빡빡해 매매가 거의 안 나올 때, 원인을 지목하기 위한 진단이다.
    자동 판정이 아예 불가능한 항목(수동 확인 전용)은 백테스트에서 항상 탈락시키므로
    별도로 구분한다 — 규칙이 나쁜 게 아니라 측정이 불가능한 것이다.
    """
    core = [it["id"] for g in ruleset["groups"] for it in g["items"] if it.get("core")]
    n = len(signals) or 1
    rows = []
    for iid in core:
        judged = sum(1 for s in signals if iid in s["items"])
        blocked = sum(1 for s in signals if s["items"].get(iid) is not True)
        rows.append({
            "id": iid,
            "blocked": blocked,
            "blocked_pct": round(blocked / n * 100, 1),
            "auto_judged": judged,
            "manual_only": judged == 0,
        })
    rows.sort(key=lambda r: -r["blocked"])
    return rows


def score_distribution(signals: list[dict], bins=(0, 50, 60, 70, 80, 85, 90, 101)) -> list[dict]:
    """룰 점수 분포 — 문턱(85)에 얼마나 근접하는지 본다."""
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        c = sum(1 for s in signals if lo <= s["rule_pct"] < hi)
        out.append({"range": f"{lo}~{hi - 1}", "n": c})
    return out


def propose_weights(ruleset: dict, lifts: list[dict]) -> dict:
    """유의한 양의 lift 에 비례해 가중치를 재배분한다.

    - 유의하지 않은 항목(|t|<2)과 음의 lift 항목은 바닥 가중치만 준다.
    - 전체 가중치 총합은 현재 룰셋과 같게 유지해 점수 스케일을 보존한다.
    - core 플래그는 건드리지 않는다. 통계가 아니라 매매 철학의 영역이다.
    """
    cur = {}
    for g in ruleset["groups"]:
        for it in g["items"]:
            cur[it["id"]] = float(it.get("w", 0))
    budget = sum(cur.values())

    lift_of = {x["id"]: x for x in lifts}
    FLOOR = 2.0
    raw = {}
    for iid, w in cur.items():
        x = lift_of.get(iid)
        if x and x.get("significant") and (x.get("lift") or 0) > 0:
            raw[iid] = x["lift"]
        else:
            raw[iid] = 0.0

    pos_total = sum(raw.values())
    n = len(cur)
    if pos_total <= 0:
        # 통계적으로 유의한 항목이 하나도 없다 — 균등 배분이 정직하다
        return {iid: round(budget / n, 1) for iid in cur}

    free = budget - FLOOR * n
    if free <= 0:
        return {iid: round(budget / n, 1) for iid in cur}
    return {iid: round(FLOOR + free * raw[iid] / pos_total, 1) for iid in cur}


def apply_weights(ruleset: dict, weights: dict) -> dict:
    """가중치를 갈아끼운 룰셋 사본."""
    import copy
    rs = copy.deepcopy(ruleset)
    for g in rs["groups"]:
        for it in g["items"]:
            if it["id"] in weights:
                it["w"] = weights[it["id"]]
    return rs


def rescore(sig: dict, ruleset: dict) -> int:
    """이미 판정된 항목 결과로 가중치만 바꿔 점수를 다시 계산한다."""
    got = tot = 0.0
    for g in ruleset["groups"]:
        for it in g["items"]:
            w = float(it.get("w", 0))
            tot += w
            if sig["items"].get(it["id"]) is True:
                got += w
    return round(got / tot * 100) if tot else 0


# ── 파라미터 스윕 ─────────────────────────────────────────────────────
def sweep(bt, signals: list[dict], grid: dict, base: Params) -> list[dict]:
    """하나씩 바꿔가며 성과를 비교한다. 신호는 재사용하므로 빠르다."""
    rows = []
    for field, values in grid.items():
        for v in values:
            p = Params(**{**asdict(base), field: v})
            s = stats(bt.simulate(signals, p))
            rows.append({"param": field, "value": v, **{
                k: s.get(k) for k in ("n", "win_rate", "expectancy_r", "total_r",
                                      "max_drawdown_r", "profit_factor")}})
    return rows


# ── 리포트 ────────────────────────────────────────────────────────────
def fmt_stats(title: str, s: dict) -> str:
    if not s.get("n"):
        return f"### {title}\n\n매매 없음\n"
    return (
        f"### {title}\n\n"
        f"| 지표 | 값 |\n|---|---|\n"
        f"| 매매 횟수 | {s['n']} |\n"
        f"| 승률 | {s['win_rate']}% |\n"
        f"| 평균 수익 R | +{s['avg_win_r']} |\n"
        f"| 평균 손실 R | {s['avg_loss_r']} |\n"
        f"| **기대값** | **{s['expectancy_r']:+.3f}R** |\n"
        f"| 누적 R | {s['total_r']:+.1f} |\n"
        f"| Profit Factor | {s['profit_factor']} |\n"
        f"| 최대 낙폭 | {s['max_drawdown_r']}R |\n"
        f"| 최대 연속 손실 | {s['max_consec_losses']}회 |\n"
        f"| 평균 보유 | {s['avg_bars']}일 |\n"
        f"| 청산 사유 | {', '.join(f'{k} {v}' for k, v in s['exit_reasons'].items())} |\n"
    )
