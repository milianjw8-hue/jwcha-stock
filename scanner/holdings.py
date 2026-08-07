#!/usr/bin/env python3
"""보유 종목을 스캔 후보와 똑같이 계산해 앱에 실어준다.

보유 종목은 대개 오늘의 스캔 후보에 들어 있지 않다. 그래서 아무 것도 하지 않으면
앱의 포지션 카드에 현재가도 패턴 경보도 뜨지 않는다. 이 모듈이 그 구멍을 메운다.

티커 목록은 POSITIONS_JSON 시크릿에서 가져온다. 앱의 "감시용 JSON 복사" 버튼이
만드는 배열이며, 장중 감시(watch_positions.py)와 입력을 공유한다 — 보유 목록을
관리할 곳이 한 군데로 유지된다.

**시크릿에서 꺼내는 값은 시장(mkt)과 티커(tick) 뿐이다.** 수량·평단가·손절가는
읽지도 반환하지도 않는다. 이 모듈의 출력은 공개 저장소에 커밋되기 때문이다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

# 보유 종목 수에 비례해 시세 조회가 늘어난다. 스캔 시간이 무한정 늘어나지 않도록 막는다.
MAX_HOLDINGS = 30


def held_tickers(market: str) -> list[str]:
    """POSITIONS_JSON 에서 해당 시장의 티커만 순서 유지·중복 제거해 뽑는다."""
    raw = os.environ.get("POSITIONS_JSON", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"POSITIONS_JSON 파싱 실패: {e}")
        return []
    if not isinstance(data, list):
        print("POSITIONS_JSON 이 배열이 아니다 — 보유 종목 계산 생략")
        return []

    out: list[str] = []
    for p in data:
        if not isinstance(p, dict) or p.get("mkt") != market:
            continue
        t = str(p.get("tick") or "").strip()
        if market == "us":
            t = t.upper()
        if t and t not in out:
            out.append(t)

    if len(out) > MAX_HOLDINGS:
        print(f"보유 종목 {len(out)}개 — 상위 {MAX_HOLDINGS}개만 계산한다")
        out = out[:MAX_HOLDINGS]
    return out


def fetch_kr(tickers: list[str], date: str, skip: set[str]) -> tuple[list[dict], dict]:
    """국장 보유 종목의 일봉을 받아 후보와 같은 모양의 dict 를 만든다.

    skip: 이미 스캔 후보에 들어 있는 티커 — 중복 계산·중복 표시를 막는다.
    """
    from pykrx import stock

    entries, frames = [], {}
    frm = (dt.datetime.strptime(date, "%Y%m%d") - dt.timedelta(days=400)).strftime("%Y%m%d")
    for t in tickers:
        if t in skip:
            continue
        try:
            df = stock.get_market_ohlcv(frm, date, t)
        except Exception as e:
            print(f"보유 {t} 조회 실패: {e}")
            continue
        time.sleep(0.15)  # KRX 서버 부하 방지
        if df is None or df.empty:
            print(f"보유 {t} 데이터 없음 — 티커를 확인하세요")
            continue
        try:
            name = stock.get_market_ticker_name(t)
        except Exception:
            name = t
        entries.append({"ticker": t, "name": name,
                        "close": int(df["종가"].iloc[-1]), "hold": True})
        frames[t] = df
    return entries, frames


def fetch_us(tickers: list[str], skip: set[str], have: dict | None = None) -> tuple[list[dict], dict]:
    """미장 보유 종목의 일봉. have 에 이미 받아둔 프레임이 있으면 재다운로드하지 않는다."""
    import yfinance as yf

    have = have or {}
    entries, frames = [], {}
    need = [t for t in tickers if t not in skip and t not in have]
    got: dict = {}
    if need:
        data = yf.download(need, period="2y", interval="1d", group_by="ticker",
                           progress=False, auto_adjust=True, threads=True)
        for t in need:
            try:
                got[t] = (data[t] if len(need) > 1 else data).dropna()
            except (KeyError, AttributeError) as e:
                print(f"보유 {t} 조회 실패: {e}")

    for t in tickers:
        if t in skip:
            continue
        df = have.get(t) if t in have else got.get(t)
        if df is None or df.empty:
            print(f"보유 {t} 데이터 없음 — 티커를 확인하세요")
            continue
        entries.append({"ticker": t, "name": t,
                        "close": round(float(df["Close"].iloc[-1]), 2), "hold": True})
        frames[t] = df
    return entries, frames


def summarize(entries: list[dict]) -> list[str]:
    """텔레그램에 붙일 보유 종목 요약. 경보가 있는 것만 줄을 만든다."""
    lines = []
    for c in entries:
        m = c.get("metrics") or {}
        warn = []
        if m.get("three_black_crows"):
            warn.append("흑삼병")
        if m.get("topping_alert"):
            warn.append("천정 패턴")
        if m.get("distribution_alert"):
            warn.append("분산 경보")
        if m.get("ma_aligned_bear"):
            warn.append("역배열")
        if m.get("close") is not None and m.get("ma20") is not None and m["close"] < m["ma20"]:
            warn.append("20일선 이탈")
        if warn:
            lines.append(f"⚠️ {c.get('name', c['ticker'])} — {' · '.join(warn)}")
    return lines
