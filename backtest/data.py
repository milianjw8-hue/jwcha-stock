#!/usr/bin/env python3
"""백테스트용 과거 데이터 수집·캐시.

생존편향(survivorship bias)에 대한 입장:
- 국장: 각 리밸런스 시점의 상장 종목 목록을 그 시점 기준으로 뽑는다. 지금 상장폐지된
  종목도 그때 거래대금 상위였다면 유니버스에 들어온다 → 생존편향 없음.
- 미장: 무료 소스로 시점별 지수 구성종목을 얻기 어렵다. 현재 구성종목을 쓰므로
  **생존편향이 남는다**. 리포트에 경고를 남기고, 결과를 낙관 편향으로 해석해야 한다.
  (같은 이유로 미장 결과는 국장 결과보다 신뢰도가 낮다.)
"""
from __future__ import annotations

import datetime as dt
import os
import time

import pandas as pd

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
KR_COLS = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}


def _cache_path(market: str, ticker: str) -> str:
    d = os.path.join(CACHE, market)
    os.makedirs(d, exist_ok=True)
    safe = str(ticker).replace("/", "_")
    return os.path.join(d, f"{safe}.csv")


def _read_cache(market: str, ticker: str) -> pd.DataFrame | None:
    p = _cache_path(market, ticker)
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        return df if len(df) else None
    except Exception:
        return None


def _write_cache(market: str, ticker: str, df: pd.DataFrame) -> None:
    df.to_csv(_cache_path(market, ticker))


# ── 국장 ──────────────────────────────────────────────────────────────
def kr_universe(start: str, end: str, top_n: int = 200, every_days: int = 63) -> list[str]:
    """분기마다 거래대금 상위 top_n 을 뽑아 합집합. 시점 기준이라 생존편향이 없다.

    KRX_ID / KRX_PW 가 필요하다 (pykrx 가 data.krx.co.kr 로그인을 요구한다).

    분기별 스냅샷을 개별 캐시하므로, 중간에 끊겨도 재실행하면 이미 받은 분기는
    건너뛴다. 수동 실행에서 가장 자주 겪는 '처음부터 다시'를 막기 위한 것이다.
    """
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'scanner'))
    from scan_kr import require_krx_credentials
    require_krx_credentials()
    from pykrx import stock

    snap_dir = os.path.join(CACHE, "kr_universe")
    os.makedirs(snap_dir, exist_ok=True)

    tickers: set[str] = set()
    cur = dt.datetime.strptime(start, "%Y%m%d")
    last = dt.datetime.strptime(end, "%Y%m%d")
    while cur <= last:
        d = stock.get_nearest_business_day_in_a_week(cur.strftime("%Y%m%d"), prev=True)
        cache_f = os.path.join(snap_dir, f"{d}_{top_n}.txt")
        if os.path.exists(cache_f):
            with open(cache_f) as f:
                got = {ln.strip() for ln in f if ln.strip()}
            tickers |= got
            print(f"  {d}: 캐시 {len(got)}종목 (누적 {len(tickers)})")
        else:
            try:
                snap = stock.get_market_ohlcv(d, market="ALL")
                snap = snap[snap["종가"] >= 2000]
                top = snap.sort_values("거래대금", ascending=False).head(top_n)
                tickers |= set(top.index)
                with open(cache_f, "w") as f:
                    f.write("\n".join(map(str, top.index)))
                print(f"  {d}: 상위 {len(top)}종목 (누적 {len(tickers)})")
            except Exception as e:
                print(f"  {d} 유니버스 조회 실패: {e}")
            time.sleep(0.3)
        cur += dt.timedelta(days=every_days)
    return sorted(tickers)


def kr_ohlcv(ticker: str, start: str, end: str, use_cache: bool = True) -> pd.DataFrame | None:
    if use_cache:
        c = _read_cache("kr", ticker)
        if c is not None:
            return c
    from pykrx import stock
    try:
        df = stock.get_market_ohlcv(start, end, ticker)
    except Exception as e:
        print(f"  {ticker} 조회 실패: {e}")
        return None
    time.sleep(0.15)
    if df is None or df.empty:
        return None
    df = df.rename(columns=KR_COLS)[["open", "high", "low", "close", "volume"]]
    df = df[df["close"] > 0]
    if len(df) < 260:
        return None
    _write_cache("kr", ticker, df)
    return df


def kr_index(start: str, end: str, code: str = "1001") -> pd.DataFrame:
    """KOSPI(1001) 지수 — 시장 필터용."""
    from pykrx import stock
    idx = stock.get_index_ohlcv(start, end, code)
    return idx.rename(columns=KR_COLS)[["open", "high", "low", "close", "volume"]]


# ── 미장 ──────────────────────────────────────────────────────────────
def us_universe() -> list[str]:
    """현재 S&P500 + Nasdaq-100. 시점별 구성이 아니라 생존편향이 남는다."""
    tickers: set[str] = set()
    try:
        sp = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        tickers |= set(sp["Symbol"].astype(str).str.replace(".", "-", regex=False))
    except Exception as e:
        print(f"  S&P500 목록 실패: {e}")
    try:
        for t in pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100"):
            for col in ("Ticker", "Symbol"):
                if col in t.columns:
                    tickers |= set(t[col].astype(str).str.replace(".", "-", regex=False))
                    break
    except Exception as e:
        print(f"  Nasdaq-100 목록 실패: {e}")
    if len(tickers) < 50:
        fb = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "scanner", "universe_us.txt")
        with open(fb) as f:
            tickers = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
    return sorted(t for t in tickers if t.isascii() and t)


def us_bulk(tickers: list[str], start: str, end: str, use_cache: bool = True) -> dict[str, pd.DataFrame]:
    """yfinance 일괄 다운로드 후 종목별 캐시."""
    out: dict[str, pd.DataFrame] = {}
    need = []
    for t in tickers:
        c = _read_cache("us", t) if use_cache else None
        if c is not None:
            out[t] = c
        else:
            need.append(t)
    if not need:
        return out

    import yfinance as yf
    print(f"  yfinance 다운로드 {len(need)}종목…")
    for i in range(0, len(need), 100):
        chunk = need[i:i + 100]
        data = yf.download(chunk, start=start, end=end, interval="1d", group_by="ticker",
                           progress=False, auto_adjust=True, threads=True)
        for t in chunk:
            try:
                df = (data[t] if len(chunk) > 1 else data).dropna()
            except KeyError:
                continue
            df = df.rename(columns={c: str(c).lower() for c in df.columns})
            cols = ["open", "high", "low", "close", "volume"]
            if not all(c in df.columns for c in cols) or len(df) < 260:
                continue
            df = df[cols]
            df = df[df["close"] > 0]
            _write_cache("us", t, df)
            out[t] = df
        print(f"  {min(i + 100, len(need))}/{len(need)}")
    return out


def us_index(start: str, end: str, symbol: str = "SPY") -> pd.DataFrame | None:
    d = us_bulk([symbol], start, end)
    return d.get(symbol)
