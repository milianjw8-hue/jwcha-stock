#!/usr/bin/env python3
"""백테스트 실행 — 결과를 docs/backtest_<market>.json / .md 로 저장한다.

    python backtest/run.py --market us --start 2019-01-01 --end 2026-08-01
    python backtest/run.py --market kr --universe 150 --no-sweep

시장 데이터가 필요하므로 GitHub Actions 러너에서 실행하는 것을 전제로 한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))

import analyze as A          # noqa: E402
import data as D             # noqa: E402
from engine import Backtest, Params  # noqa: E402
from rules import load_ruleset       # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs")

SWEEP_GRID = {
    "stop_pct": [3, 5, 7, 8],
    "trail_mode": ["ma5", "ma10", "ma20", "atr", "pct"],
    "trail_param": [1.5, 2.0, 2.5, 3.0],
    "first_target_r": [1.5, 2.0, 3.0],
    "max_open": [4, 8, 12],
}


def load_frames(market: str, start: str, end: str, universe_n: int, limit: int = 0):
    if market == "kr":
        s, e = start.replace("-", ""), end.replace("-", "")
        print("국장 유니버스 구성 (시점 기준 — 생존편향 없음)…")
        tickers = D.kr_universe(s, e, top_n=universe_n)
        if limit:
            tickers = tickers[:limit]
        print(f"유니버스 {len(tickers)}종목")
        frames = {}
        for i, t in enumerate(tickers, 1):
            df = D.kr_ohlcv(t, s, e)
            if df is not None:
                frames[t] = df
            if i % 50 == 0:
                print(f"  {i}/{len(tickers)} (수집 {len(frames)})")
        idx = D.kr_index(s, e)
        return frames, idx
    tickers = D.us_universe()
    if limit:
        tickers = tickers[:limit]
    print(f"미장 유니버스 {len(tickers)}종목 (현재 구성 — 생존편향 있음)")
    frames = D.us_bulk(tickers, start, end)
    return frames, D.us_index(start, end)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["us", "kr"], required=True)
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--split", default="2024-01-01", help="학습/검증 분할 기준일")
    ap.add_argument("--universe", type=int, default=200, help="국장 분기별 상위 N종목")
    ap.add_argument("--no-sweep", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="유니버스를 N종목으로 제한 (스모크 테스트용)")
    ap.add_argument("--quick", action="store_true",
                    help="빠른 점검 — 30종목·2년·스윕 생략. 본 실행 전 파이프라인 확인용")
    args = ap.parse_args()

    if args.quick:
        args.limit = args.limit or 30
        args.no_sweep = True
        if args.start == "2019-01-01":
            args.start = (dt.date.today() - dt.timedelta(days=730)).isoformat()
        if args.split == "2024-01-01":
            args.split = (dt.date.today() - dt.timedelta(days=365)).isoformat()
        print(f"[quick] {args.limit}종목 · {args.start}~ · 스윕 생략 — 결과는 참고용")

    frames, index_df = load_frames(args.market, args.start, args.end,
                                   args.universe, args.limit)
    MIN_SYMBOLS = 10 if args.quick else 20
    if len(frames) < MIN_SYMBOLS:
        print(f"데이터 부족 ({len(frames)}종목, 최소 {MIN_SYMBOLS}) — 중단.\n"
              "  네트워크 또는 데이터 소스 문제일 수 있다. backtest/cache/ 를 지우고 재시도.")
        sys.exit(1)

    base = Params()
    print(f"\n종목 {len(frames)}개로 시뮬레이션 시작…")

    # 연구 모드 — 룰셋 통과 여부와 무관하게 사전필터 통과 바에서 모두 진입.
    # 항목별 판별력을 재려면 충족·미충족 양쪽 표본이 필요하다.
    bt = Backtest(args.market, frames, index_df, base, research=True)
    signals = bt.collect_signals()
    print(f"사전필터 통과 신호 {len(signals)}건")
    if not signals:
        print("신호 없음 — 중단")
        sys.exit(1)

    research_trades = bt.simulate(signals, base)
    print(f"연구 모드 매매 {len(research_trades)}건")

    # 현행 룰셋(진입 가능 판정만)으로 걸렀을 때
    live_trades = bt.simulate(signals, base, accept=lambda s: s["verdict"] == "진입 가능")
    print(f"현행 룰셋 매매 {len(live_trades)}건")

    ruleset = load_ruleset("us_minervini" if args.market == "us" else "kr_leader")

    # ── 학습/검증 분리 ────────────────────────────────────────────
    r_train, r_test = A.split_by_date(research_trades, args.split)
    lifts_train = A.item_lift(r_train)
    weights = A.propose_weights(ruleset, lifts_train)
    tuned = A.apply_weights(ruleset, weights)

    # 학습기간에서 뽑은 가중치를 그대로 두고 전 기간을 다시 돌린 뒤 분할한다.
    # 검증기간 매매에 검증기간 정보가 쓰이지 않아야 유지율이 의미를 갖는다.
    THRESH = tuned.get("verdict", {}).get("go", 85)
    tuned_all = bt.simulate(signals, base, accept=lambda s: A.rescore(s, tuned) >= THRESH)
    t_train, t_test = A.split_by_date(tuned_all, args.split)

    st = {
        "research_all": A.stats(research_trades),
        "research_train": A.stats(r_train),
        "research_test": A.stats(r_test),
        "live_all": A.stats(live_trades),
        "live_train": A.stats(A.split_by_date(live_trades, args.split)[0]),
        "live_test": A.stats(A.split_by_date(live_trades, args.split)[1]),
        "tuned_train": A.stats(t_train),
        "tuned_test": A.stats(t_test),
    }

    # 과최적화 판정 — 검증기간 기대값이 학습기간의 60% 미만이면 경고
    e_tr = st["tuned_train"].get("expectancy_r") or 0
    e_te = st["tuned_test"].get("expectancy_r") or 0
    retention = (e_te / e_tr) if e_tr > 0 else None
    overfit = retention is not None and retention < 0.6

    sweeps = [] if args.no_sweep else A.sweep(bt, signals, SWEEP_GRID, base)

    result = {
        "market": args.market,
        "asof": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "period": {"start": args.start, "end": args.end, "split": args.split},
        "universe_size": len(frames),
        "signals": len(signals),
        "params": asdict(base),
        "stats": st,
        "retention": None if retention is None else round(retention, 2),
        "overfit_warning": overfit,
        "item_lift_train": lifts_train,
        "proposed_weights": weights,
        "core_blockers": A.core_blockers(signals, ruleset),
        "score_distribution": A.score_distribution(signals),
        "sweep": sweeps,
        "survivorship_bias": args.market == "us",
        "quick": args.quick,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"backtest_{args.market}.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    md = report_md(result)
    with open(os.path.join(OUT_DIR, f"backtest_{args.market}.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print("\n" + md)


def report_md(r: dict) -> str:
    m = "미국장" if r["market"] == "us" else "국내장"
    st = r["stats"]
    out = [f"# 백테스트 리포트 — {m}", "",
           f"- 기간: {r['period']['start']} ~ {r['period']['end']} "
           f"(학습/검증 분할 {r['period']['split']})",
           f"- 유니버스: {r['universe_size']}종목 · 사전필터 통과 신호 {r['signals']}건",
           f"- 규칙: `docs/rules/` — 앱·스캐너와 동일한 파일", ""]

    if r.get("quick"):
        out += ["> ⚠️ **quick 모드 결과다.** 종목 수·기간을 줄이고 스윕을 생략했다. "
                "파이프라인 동작 확인용이며 의사결정 근거로 쓸 수 없다.", ""]

    if r["survivorship_bias"]:
        out += ["> ⚠️ **생존편향 있음.** 미장 유니버스는 현재 지수 구성종목이라 그동안 "
                "퇴출된 종목이 빠져 있다. 실제보다 성과가 좋게 나온다. 국장 결과는 "
                "시점별 종목 목록을 써서 이 편향이 없다.", ""]

    out += ["## 1. 현행 룰셋 성과", "",
            "지금 앱에 들어 있는 가중치로 '진입 가능' 판정이 난 신호만 매매한 결과.", "",
            A.fmt_stats("전체 기간", st["live_all"]),
            A.fmt_stats("학습기간", st["live_train"]),
            A.fmt_stats("검증기간", st["live_test"]), ""]

    out += ["## 2. 기준선 — 사전필터만 통과한 전체 매매", "",
            "52주 고점 근접 + 거래량 증가만 보고 전부 진입했을 때. "
            "룰셋이 이 기준선보다 나아야 존재 의미가 있다.", "",
            A.fmt_stats("전체 기간", st["research_all"]), ""]

    base_e = st["research_all"].get("expectancy_r")
    live_e = st["live_all"].get("expectancy_r")
    if not st["live_all"].get("n"):
        out += ["> ⚠️ **현행 룰셋으로 체결된 매매가 0건이다.** 규칙이 너무 빡빡해 실전에서도 "
                "거의 진입하지 못한다는 뜻이다. 아래 3절에서 어느 항목이 막고 있는지 확인하고, "
                "core 지정을 줄이거나 판정 문턱(verdict.go)을 낮춰야 한다.", ""]
    elif base_e is not None and live_e is not None:
        verdict = "룰셋이 기준선을 개선했다" if live_e > base_e else \
                  "**룰셋이 기준선을 개선하지 못했다** — 규칙을 단순화하거나 재설계해야 한다"
        out += [f"→ 기준선 {base_e:+.3f}R vs 룰셋 {live_e:+.3f}R — {verdict}", ""]

    out += ["## 3. 무엇이 신호를 탈락시키는가", "",
            "core 항목별 탈락 비율. 100%에 가까우면 그 항목 하나가 룰셋 전체를 막고 있다.", "",
            "| core 항목 | 탈락 | 비율 | 비고 |", "|---|---|---|---|"]
    for b in r["core_blockers"]:
        note = "**자동 판정 불가 — 백테스트에서 항상 탈락**" if b["manual_only"] else ""
        out.append(f"| `{b['id']}` | {b['blocked']} | {b['blocked_pct']}% | {note} |")
    out += ["", "룰 점수 분포 (문턱 85점):", "",
            "| 점수대 | 신호 수 |", "|---|---|"]
    for s in r["score_distribution"]:
        out.append(f"| {s['range']} | {s['n']} |")
    out.append("")

    out += ["## 4. 항목별 판별력", "",
            "충족했을 때와 아닐 때의 평균 R 차이. |t| ≥ 2 가 아니면 우연과 구분되지 않는다.", "",
            "| 항목 | 충족 n | 미충족 n | 충족 평균R | 미충족 평균R | lift | t | 유의 |",
            "|---|---|---|---|---|---|---|---|"]
    for x in r["item_lift_train"]:
        if x.get("lift") is None:
            out.append(f"| `{x['id']}` | {x['n_yes']} | {x['n_no']} | — | — | — | — | 표본부족 |")
        else:
            out.append(f"| `{x['id']}` | {x['n_yes']} | {x['n_no']} | {x['mean_r_yes']:+.3f} | "
                       f"{x['mean_r_no']:+.3f} | {x['lift']:+.3f} | {x['t']} | "
                       f"{'✅' if x['significant'] else '—'} |")

    out += ["", "## 5. 제안 가중치", "",
            "학습기간의 유의한 양의 lift 에 비례해 재배분했다. 총합은 현행과 같게 유지한다.", "",
            "| 항목 | 제안 w |", "|---|---|"]
    for k, v in r["proposed_weights"].items():
        out.append(f"| `{k}` | {v} |")

    out += ["", "## 6. 과최적화 검증", "",
            "학습기간에서 뽑은 가중치를 검증기간에 그대로 적용한 결과.", "",
            A.fmt_stats("학습기간 (가중치 튜닝됨)", st["tuned_train"]),
            A.fmt_stats("검증기간 (같은 가중치)", st["tuned_test"]), ""]
    if r["retention"] is None:
        out += ["→ 학습기간 기대값이 0 이하라 유지율을 계산할 수 없다. "
                "가중치 튜닝 자체가 무의미하다.", ""]
    elif r["overfit_warning"]:
        out += [f"→ ⚠️ **유지율 {r['retention']:.0%} — 과최적화.** 검증기간 성과가 학습기간의 "
                "60% 미만이다. 이 가중치를 앱에 반영하면 안 된다. 규칙을 단순화하라.", ""]
    else:
        out += [f"→ 유지율 {r['retention']:.0%} — 기준(60%) 통과. 가중치를 반영해도 된다.", ""]

    if r["sweep"]:
        out += ["## 7. 파라미터 스윕", "",
                "| 파라미터 | 값 | n | 승률 | 기대값 | 누적R | MDD | PF |",
                "|---|---|---|---|---|---|---|---|"]
        for s in r["sweep"]:
            out.append(f"| {s['param']} | {s['value']} | {s['n']} | {s['win_rate']}% | "
                       f"{s['expectancy_r']:+.3f}R | {s['total_r']:+.1f} | "
                       f"{s['max_drawdown_r']}R | {s['profit_factor']} |")
        out += ["", "> 스윕 결과에서 최고값을 그대로 고르면 그 자체가 과최적화다. "
                "성과가 평평한 구간의 중앙값을 고르는 편이 안전하다.", ""]

    out += ["---", "",
            "이 리포트는 과거 데이터에 대한 시뮬레이션이며 미래 수익을 보장하지 않는다. "
            "수수료·세금·슬리피지는 반영되어 있지 않다."]
    return "\n".join(out)


if __name__ == "__main__":
    main()
