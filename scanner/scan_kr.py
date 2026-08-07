#!/usr/bin/env python3
"""국내장 스윙 스캐너 — 거래대금 상위 종목 중 52주 고점 근접 + 거래량 급증 + 돌파.

docs/results.json 으로 저장한다. docs/index.html 의 자동 스캔 결과
카드가 읽는 스키마:
{
  "asof": "YYYY-MM-DD HH:MM KST",
  "market_ok": true,
  "candidates": [
    {"name","breakout","vol_vs_20d","pct_of_52wk_high", ...}
  ],
  "holdings": [                      // 스캔을 통과하지 못한 보유 종목 (POSITIONS_JSON)
    {"ticker","name","close","hold":true,"metrics","rule"}
  ]
}
"""
import json
import os
import sys
import time
import datetime as dt
from zoneinfo import ZoneInfo

from pykrx import stock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telegram_push import send_telegram
from enrich import enrich, summarize
import holdings as H

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "docs", "results.json")

TOP_BY_VALUE = 150        # 당일 거래대금 상위 N종목만 정밀 스캔
MIN_PRICE = 2000          # 동전주 제외
MIN_PCT_OF_HIGH = 80      # 52주 고점 대비 80% 이상
MIN_VOL_RATIO = 2.0       # 20일 평균 거래량 대비 2배 이상
BREAKOUT_LOOKBACK = 60    # 직전 60거래일 고가 돌파 = 🔥
MAX_CANDIDATES = 15


KRX_HELP = """
❌ KRX 계정 정보가 없습니다 (KRX_ID / KRX_PW).

   pykrx 는 data.krx.co.kr 로그인을 요구합니다. 계정 없이는 빈 데이터가 돌아와
   엉뚱한 곳에서 오류가 납니다.

   GitHub Actions:  저장소 Settings → Secrets and variables → Actions →
                    New repository secret 으로 KRX_ID, KRX_PW 등록
   로컬 실행:        export KRX_ID=아이디  export KRX_PW=비밀번호
                    (Windows PowerShell: $env:KRX_ID="아이디")
"""


def require_krx_credentials() -> None:
    """국장 데이터 조회 전에 자격증명을 확인하고, 없으면 원인을 알리고 멈춘다."""
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        print(KRX_HELP)
        sys.exit(1)

def latest_trading_day() -> str:
    return stock.get_nearest_business_day_in_a_week()


def market_filter(date: str) -> bool:
    """KOSPI 종가가 20일선·60일선 위면 시장 필터 통과."""
    frm = (dt.datetime.strptime(date, "%Y%m%d") - dt.timedelta(days=200)).strftime("%Y%m%d")
    idx = stock.get_index_ohlcv(frm, date, "1001")  # KOSPI
    close = idx["종가"]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ok = bool(close.iloc[-1] > ma20 and close.iloc[-1] > ma60)
    print(f"시장필터 KOSPI: close={close.iloc[-1]:.1f} MA20={ma20:.1f} MA60={ma60:.1f} → {'통과' if ok else '미통과'}")
    return ok


def prev_business_days(date: str, n: int) -> list[str]:
    """date 이전 n 영업일 (date 제외)."""
    out, cur = [], dt.datetime.strptime(date, "%Y%m%d")
    while len(out) < n:
        cur -= dt.timedelta(days=1)
        d = stock.get_nearest_business_day_in_a_week(cur.strftime("%Y%m%d"), prev=True)
        if d not in out and d < date:
            out.append(d)
            cur = dt.datetime.strptime(d, "%Y%m%d")
    return out


def top_value_history(date: str, days: int = 2) -> dict[str, int]:
    """직전 영업일들의 거래대금 상위 집합 — '상위권 N일 유지' 판정용."""
    counts: dict[str, int] = {}
    for d in prev_business_days(date, days):
        try:
            snap = stock.get_market_ohlcv(d, market="ALL")
        except Exception as e:
            print(f"{d} 거래대금 상위 조회 실패: {e}")
            continue
        time.sleep(0.2)
        top = snap.sort_values("거래대금", ascending=False).head(TOP_BY_VALUE)
        for t in top.index:
            counts[t] = counts.get(t, 0) + 1
    return counts


def buy_streak(ticker: str, date: str) -> int | None:
    """외국인 또는 기관의 연속 순매수 일수 (둘 중 긴 쪽)."""
    frm = (dt.datetime.strptime(date, "%Y%m%d") - dt.timedelta(days=30)).strftime("%Y%m%d")
    try:
        df = stock.get_market_trading_value_by_date(frm, date, ticker)
    except Exception as e:
        print(f"{ticker} 수급 조회 실패: {e}")
        return None
    time.sleep(0.15)
    best = 0
    for col in ("외국인합계", "기관합계"):
        if col not in df.columns:
            continue
        streak = 0
        for v in reversed(df[col].tolist()):
            if v > 0:
                streak += 1
            else:
                break
        best = max(best, streak)
    return best


def scan(date: str) -> list[dict]:
    snap = stock.get_market_ohlcv(date, market="ALL")  # 전종목 당일 OHLCV
    snap = snap[snap["종가"] >= MIN_PRICE]
    top = snap.sort_values("거래대금", ascending=False).head(TOP_BY_VALUE)
    print(f"거래대금 상위 {len(top)}종목 정밀 스캔…")

    frm = (dt.datetime.strptime(date, "%Y%m%d") - dt.timedelta(days=400)).strftime("%Y%m%d")
    candidates = []
    frames: dict[str, "object"] = {}
    for ticker in top.index:
        try:
            df = stock.get_market_ohlcv(frm, date, ticker)
        except Exception as e:
            print(f"{ticker} 조회 실패: {e}")
            continue
        time.sleep(0.15)  # KRX 서버 부하 방지
        if len(df) < 120:  # 신규 상장 제외 (최소 약 6개월)
            continue
        close, high, vol = df["종가"], df["고가"], df["거래량"]
        c = float(close.iloc[-1])
        hi52 = float(high.iloc[-252:].max())
        pct_of_high = c / hi52 * 100
        if pct_of_high < MIN_PCT_OF_HIGH:
            continue
        vol_ma20 = vol.iloc[-21:-1].mean()
        if vol_ma20 <= 0:
            continue
        vol_ratio = float(vol.iloc[-1] / vol_ma20)
        if vol_ratio < MIN_VOL_RATIO:
            continue
        prior_high = float(high.iloc[-(BREAKOUT_LOOKBACK + 1):-1].max())
        breakout = bool(c > prior_high)
        candidates.append({
            "ticker": ticker,
            "name": stock.get_market_ticker_name(ticker),
            "breakout": breakout,
            "vol_vs_20d": round(vol_ratio, 1),
            "pct_of_52wk_high": round(pct_of_high),
            "close": int(c),
            "value_krw_bn": round(float(top.loc[ticker, "거래대금"]) / 1e9, 1),
        })
        frames[ticker] = df

    candidates.sort(key=lambda r: (not r["breakout"], -r["vol_vs_20d"]))
    return candidates[:MAX_CANDIDATES], frames, set(top.index)


def main():
    require_krx_credentials()
    now_kst = dt.datetime.now(ZoneInfo("Asia/Seoul"))
    date = latest_trading_day()
    print(f"기준일: {date}")
    market_ok = market_filter(date)
    cands, frames, top_today = scan(date)

    # 보유 종목은 스캔을 통과하지 못해도 앱이 현재가·패턴 경보를 띄울 수 있어야 한다.
    held, held_frames = H.fetch_kr(H.held_tickers("kr"), date,
                                   skip={c["ticker"] for c in cands})
    frames.update(held_frames)
    print(f"보유 종목 {len(held)}개 추가 계산")

    # 룰셋이 요구하는 수급 지표는 후보·보유에 대해서만 조회한다 (KRX 호출 절약)
    prior = top_value_history(date, days=2)
    extra = {}
    for c in cands + held:
        t = c["ticker"]
        extra[t] = {
            # 오늘 상위권에 없는 보유 종목까지 1을 더하면 실적이 부풀려진다
            "top_value_days": (1 if t in top_today else 0) + prior.get(t, 0),
            "inst_foreign_buy_streak": buy_streak(t, date),
        }
    # 같은 리스트 객체를 수정하므로 enrich 후에도 cands 와 held 는 그대로 갈라져 있다
    enrich(cands + held, frames, "kr", market_ok, extra)
    # 지표 계산에 실패한 보유 종목은 차트 파일도 없다 — 앱에 빈 카드를 남기지 않는다
    held = [c for c in held if "metrics" in c]

    result = {
        "asof": now_kst.strftime("%Y-%m-%d %H:%M KST"),
        "market_ok": market_ok,
        "candidates": cands,
        "holdings": held,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"저장: {OUT_PATH} — 후보 {len(result['candidates'])}종목")

    lines = [f"📊 <b>국장 스캔</b> {result['asof']}",
             f"시장필터: {'✅ 통과' if result['market_ok'] else '⚠️ 미통과 — 현금 우선'}"]
    for c in result["candidates"][:10]:
        lines.append(f"{'🔥돌파' if c['breakout'] else '대기'} {c['name']} "
                     f"거래량x{c['vol_vs_20d']} · 고점대비 {c['pct_of_52wk_high']}% · 대금 {c['value_krw_bn']}십억")
        badge = summarize(c)
        if badge:
            lines.append(f"    └ {badge}")
    if not result["candidates"]:
        lines.append("후보 없음")
    warn = H.summarize(held)
    if warn:
        lines.append("\n<b>보유 종목 점검</b>")
        lines.extend(warn)
    send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
