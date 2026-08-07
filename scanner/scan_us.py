#!/usr/bin/env python3
"""미국장 스윙 스캐너 — 트렌드 템플릿 + RS 백분위 + 피봇 돌파/근접.

docs/results_us.json 으로 저장한다. docs/index.html 의 자동 스캔 결과
카드가 읽는 스키마:
{
  "asof": "YYYY-MM-DD HH:MM KST",
  "market_ok": true,
  "candidates": [
    {"ticker","setup","rs_pctile","dist_to_pivot_pct","pct_of_52wk_high", ...}
  ]
}
setup: breakout(🔥 돌파) | pivot_near(🎯 피봇 근접) | base(🧱 베이스) | extended(↗ 이탈)
"""
import json
import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telegram_push import send_telegram
from enrich import enrich, summarize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "docs", "results_us.json")
FALLBACK_UNIVERSE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe_us.txt")

MIN_DOLLAR_VOL = 20e6   # 20일 평균 거래대금 $20M 이상
MIN_RS_PCTILE = 70      # RS 백분위 70 이상
MIN_PCT_OF_HIGH = 75    # 52주 고점 대비 75% 이상
MAX_CANDIDATES = 15


def load_universe() -> list[str]:
    tickers: set[str] = set()
    try:
        sp500 = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        tickers |= set(sp500["Symbol"].astype(str).str.replace(".", "-", regex=False))
        print(f"S&P500 {len(tickers)}종목 로드")
    except Exception as e:
        print(f"S&P500 목록 로드 실패: {e}")
    try:
        for tbl in pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100"):
            for col in ("Ticker", "Symbol"):
                if col in tbl.columns:
                    tickers |= set(tbl[col].astype(str).str.replace(".", "-", regex=False))
                    break
    except Exception as e:
        print(f"Nasdaq-100 목록 로드 실패: {e}")
    if len(tickers) < 50:
        print("온라인 유니버스 로드 실패 — 내장 목록 사용")
        with open(FALLBACK_UNIVERSE) as f:
            tickers = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
    return sorted(t for t in tickers if t.isascii())


def weighted_return(close: pd.Series) -> float:
    """IBD식 가중 수익률: 최근 3개월에 2배 가중."""
    def ret(n):
        if len(close) <= n or close.iloc[-1 - n] <= 0:
            return np.nan
        return close.iloc[-1] / close.iloc[-1 - n] - 1
    r3, r6, r9, r12 = ret(63), ret(126), ret(189), ret(252)
    if np.isnan(r3):
        return np.nan
    parts = [2 * r3] + [r for r in (r6, r9, r12) if not np.isnan(r)]
    return float(np.nansum(parts))


def market_filter() -> bool:
    """SPY 종가 > 50일선 > 200일선이면 시장 필터 통과."""
    spy = yf.download("SPY", period="2y", interval="1d", progress=False, auto_adjust=True)
    if spy.empty:
        return False
    close = spy["Close"].squeeze().dropna()
    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    ok = bool(close.iloc[-1] > ma50 > ma200)
    print(f"시장필터 SPY: close={close.iloc[-1]:.2f} MA50={ma50:.2f} MA200={ma200:.2f} → {'통과' if ok else '미통과'}")
    return ok


def scan(market_ok: bool) -> dict:
    universe = load_universe()
    print(f"유니버스 {len(universe)}종목 다운로드 중…")
    data = yf.download(universe, period="2y", interval="1d", group_by="ticker",
                       progress=False, auto_adjust=True, threads=True)

    rows = []
    frames: dict[str, pd.DataFrame] = {}
    for t in universe:
        try:
            df = data[t].dropna()
        except KeyError:
            continue
        if len(df) < 260:
            continue
        close, high, vol = df["Close"], df["High"], df["Volume"]
        c = float(close.iloc[-1])
        ma50 = close.rolling(50).mean().iloc[-1]
        ma150 = close.rolling(150).mean().iloc[-1]
        ma200 = close.rolling(200).mean()
        hi52 = float(high.iloc[-252:].max())
        lo52 = float(close.iloc[-252:].min())
        dollar_vol = float((close * vol).rolling(20).mean().iloc[-1])

        # 트렌드 템플릿
        if not (c > ma50 > ma150 > ma200.iloc[-1]):
            continue
        if not ma200.iloc[-1] > ma200.iloc[-21]:  # 200일선 상승 중
            continue
        if c < MIN_PCT_OF_HIGH / 100 * hi52 or c < 1.3 * lo52:
            continue
        if dollar_vol < MIN_DOLLAR_VOL:
            continue

        pivot = float(high.iloc[-21:-1].max())  # 직전 20거래일 고가 (당일 제외)
        vol_ratio = float(vol.iloc[-1] / vol.rolling(50).mean().iloc[-1])
        prev_c = float(close.iloc[-2])

        if c > pivot and prev_c <= pivot and vol_ratio >= 1.5:
            setup = "breakout"
        elif c > pivot * 1.05:
            setup = "extended"
        elif c <= pivot and (pivot - c) / c * 100 <= 3.0:
            setup = "pivot_near"
        else:
            setup = "base"

        rows.append({
            "ticker": t,
            "setup": setup,
            "wret": weighted_return(close),
            "dist_to_pivot_pct": round((pivot - c) / c * 100, 1),
            "pct_of_52wk_high": round(c / hi52 * 100),
            "close": round(c, 2),
            "vol_vs_50d": round(vol_ratio, 1),
        })
        frames[t] = df

    df = pd.DataFrame(rows)
    if df.empty:
        return {"candidates": []}
    df["rs_pctile"] = df["wret"].rank(pct=True) * 100
    df = df[df["rs_pctile"] >= MIN_RS_PCTILE]
    order = {"breakout": 0, "pivot_near": 1, "base": 2, "extended": 3}
    df = df.sort_values(["setup", "rs_pctile"],
                        key=lambda s: s.map(order) if s.name == "setup" else s,
                        ascending=[True, False])
    cands, extra = [], {}
    for _, r in df.head(MAX_CANDIDATES).iterrows():
        t = r["ticker"]
        cands.append({
            "ticker": t,
            "setup": r["setup"],
            "rs_pctile": round(float(r["rs_pctile"])),
            "dist_to_pivot_pct": float(r["dist_to_pivot_pct"]),
            "pct_of_52wk_high": int(r["pct_of_52wk_high"]),
            "close": float(r["close"]),
            "vol_vs_50d": float(r["vol_vs_50d"]),
        })
        # RS 는 유니버스 상대 순위라 개별 종목 데이터만으로는 계산할 수 없다.
        extra[t] = {"rs_pctile": round(float(r["rs_pctile"]))}

    enrich(cands, frames, "us", market_ok, extra)
    return {"candidates": cands}


def main():
    now_kst = dt.datetime.now(ZoneInfo("Asia/Seoul"))
    market_ok = market_filter()
    result = {"asof": now_kst.strftime("%Y-%m-%d %H:%M KST"), "market_ok": market_ok}
    result.update(scan(market_ok))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"저장: {OUT_PATH} — 후보 {len(result['candidates'])}종목")

    icon = {"breakout": "🔥", "pivot_near": "🎯", "base": "🧱", "extended": "↗"}
    lines = [f"📈 <b>미장 스캔</b> {result['asof']}",
             f"시장필터: {'✅ 통과' if result['market_ok'] else '⚠️ 미통과'}"]
    for c in result["candidates"][:10]:
        lines.append(f"{icon.get(c['setup'],'')} {c['ticker']} RS{c['rs_pctile']} "
                     f"피봇 {c['dist_to_pivot_pct']:+.1f}% · 고점대비 {c['pct_of_52wk_high']}%")
        badge = summarize(c)
        if badge:
            lines.append(f"    └ {badge}")
    if not result["candidates"]:
        lines.append("후보 없음")
    send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
