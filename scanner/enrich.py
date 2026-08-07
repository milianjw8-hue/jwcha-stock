#!/usr/bin/env python3
"""스캔 후보에 지표·패턴·룰셋 점수를 붙이고 차트용 OHLC 를 내보낸다.

스캐너(국장/미장) 공통 후처리. 후보 종목에 대해서만 실행하므로 비용이 작다.
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indicators as I  # noqa: E402
from rules import load_ruleset, score_ruleset  # noqa: E402

OHLC_DIR = os.path.join(ROOT, "docs", "ohlc")
RULESET_FOR = {"us": "us_minervini", "kr": "kr_leader"}
SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(market: str, key: str) -> str:
    return f"{market}_{SAFE.sub('_', str(key))}"


# pykrx 는 한글 컬럼을 반환한다
KR_COLS = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}


def enrich(candidates: list[dict], frames: dict, market: str, market_ok: bool,
           extra: dict[str, dict] | None = None,
           cols: dict[str, str] | None = None) -> list[dict]:
    """후보 리스트에 metrics/rule 을 채우고 docs/ohlc/*.json 을 쓴다.

    candidates: 스캐너가 만든 후보 dict 리스트. 'ticker' 키로 frames 를 찾는다.
    frames:     {ticker: 원본 OHLCV DataFrame}
    extra:      {ticker: {지표명: 값}} — 스캐너만 아는 값(RS, 수급 등)
    cols:       원본 컬럼명 → 표준 컬럼명 매핑 (국장은 KR_COLS)
    """
    extra = extra or {}
    if cols is None and market == "kr":
        cols = KR_COLS
    os.makedirs(OHLC_DIR, exist_ok=True)
    ruleset = load_ruleset(RULESET_FOR[market])
    keep: set[str] = set()

    for c in candidates:
        t = c.get("ticker")
        raw = frames.get(t)
        if raw is None:
            continue
        try:
            df = I.normalize(raw, cols)
            m = I.compute(df, market=market,
                          extra={"market_ok": bool(market_ok), **extra.get(t, {})})
        except Exception as e:
            print(f"{t} 지표 계산 실패: {e}")
            continue

        c["metrics"] = m
        r = score_ruleset(ruleset, m)
        c["rule"] = {"pct": r["pct"], "verdict": r["verdict"],
                     "core_miss": r["core_miss"], "auto": r["auto_resolved"],
                     "items": {i["id"]: i["ok"] for i in r["items"] if i["src"] == "auto"}}

        slug = _slug(market, t)
        keep.add(slug)
        payload = I.ohlc_payload(df)
        payload["ticker"] = t
        payload["name"] = c.get("name", t)
        payload["market"] = market
        with open(os.path.join(OHLC_DIR, f"{slug}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    # 후보에서 빠진 종목의 과거 파일은 지운다 (저장소가 무한히 커지는 것을 막는다)
    for fn in os.listdir(OHLC_DIR):
        if fn.startswith(f"{market}_") and fn.endswith(".json") and fn[:-5] not in keep:
            os.remove(os.path.join(OHLC_DIR, fn))

    return candidates


def summarize(c: dict) -> str:
    """텔레그램 한 줄 요약에 붙일 지표 배지."""
    m, r = c.get("metrics") or {}, c.get("rule") or {}
    bits = []
    if r.get("pct") is not None:
        bits.append(f"룰 {r['pct']}%")
    if m.get("bb_squeeze"):
        bits.append("🔵스퀴즈")
    if m.get("band_state") == "walk":
        bits.append("🟢밴드워킹")
    if m.get("cup_handle"):
        bits.append("☕컵앤핸들")
    if m.get("double_bottom"):
        bits.append("W쌍바닥")
    if m.get("distribution_alert"):
        bits.append("🔴분산경보")
    if m.get("topping_alert"):
        bits.append("⚠천정")
    if m.get("retest_level"):
        bits.append("↩리테스트")
    return " · ".join(bits)
