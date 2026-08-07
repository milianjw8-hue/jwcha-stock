#!/usr/bin/env python3
"""보유 포지션 장중 감시 — 손절·트레일링 스탑·청산 경보를 텔레그램으로 푸시한다.

포지션은 앱(폰)의 IndexedDB 에만 있으므로 서버가 알 수 없다. 앱의
"감시용 JSON 복사" 버튼이 만든 배열을 저장소의 POSITIONS_JSON 시크릿에 넣으면
이 스크립트가 읽는다. 공개 저장소에 포지션을 커밋하지 않기 위한 방식이다.

POSITIONS_JSON 형식:
[{"mkt":"us","tick":"NVDA","avg":120.5,"qty":30,"stop":112.0,"high":131.2,
  "trail":{"mode":"ma20","p":0},"entry0":118.0,"stop0":110.0}, ...]

같은 경보를 15분마다 반복 발송하지 않도록 상태 파일에 이미 보낸 경보를 기록한다
(GitHub Actions 캐시로 실행 간 유지).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telegram_push import send_telegram  # noqa: E402

STATE_PATH = os.environ.get("WATCH_STATE", ".watch_state.json")


def load_positions() -> list[dict]:
    raw = os.environ.get("POSITIONS_JSON", "").strip()
    if not raw:
        print("POSITIONS_JSON 미설정 — 감시할 포지션 없음")
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"POSITIONS_JSON 파싱 실패: {e}")
        return []
    return data if isinstance(data, list) else []


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, json.JSONDecodeError):
        st = {}
    # 날짜가 바뀌면 초기화 — 같은 경보를 매일 한 번은 다시 받는다
    tdy = dt.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    if st.get("date") != tdy:
        st = {"date": tdy, "sent": []}
    return st


def save_state(st: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def fetch_us(tickers: list[str]) -> dict:
    import yfinance as yf
    import indicators as I
    out = {}
    if not tickers:
        return out
    data = yf.download(tickers, period="1y", interval="1d", group_by="ticker",
                       progress=False, auto_adjust=True, threads=True)
    for t in tickers:
        try:
            df = I.normalize(data[t].dropna() if len(tickers) > 1 else data.dropna())
            out[t] = I.compute(df, market="us")
        except Exception as e:
            print(f"{t} 시세 조회 실패: {e}")
    return out


def fetch_kr(tickers: list[str]) -> dict:
    from scan_kr import require_krx_credentials
    require_krx_credentials()
    from pykrx import stock
    import indicators as I
    from enrich import KR_COLS
    out = {}
    if not tickers:
        return out
    to = stock.get_nearest_business_day_in_a_week()
    frm = (dt.datetime.strptime(to, "%Y%m%d") - dt.timedelta(days=400)).strftime("%Y%m%d")
    for t in tickers:
        try:
            df = I.normalize(stock.get_market_ohlcv(frm, to, t), KR_COLS)
            out[t] = I.compute(df, market="kr")
        except Exception as e:
            print(f"{t} 시세 조회 실패: {e}")
    return out


def effective_stop(p: dict, m: dict) -> tuple[float, str]:
    """트레일링 스탑 — 한 번 올린 손절선은 내리지 않는다."""
    stop0 = float(p.get("stop0") or p.get("stop") or 0)
    high = max(float(p.get("high") or 0), float(m.get("close") or 0))
    tr = p.get("trail") or {}
    mode, param = tr.get("mode"), float(tr.get("p") or 0)

    trail = None
    label = "고정"
    if mode == "pct" and param > 0:
        trail, label = high * (1 - param / 100), f"고점 -{param}%"
    elif mode == "atr" and param > 0 and m.get("atr14"):
        trail, label = high - m["atr14"] * param, f"ATR x{param}"
    elif mode and mode.startswith("ma") and m.get(mode) is not None:
        trail, label = float(m[mode]), mode.upper()

    stop = max(stop0, float(p.get("stop") or 0), trail or 0)
    return stop, (label if trail and trail >= stop0 else "고정")


def check(p: dict, m: dict) -> list[str]:
    alerts = []
    close = m.get("close")
    if close is None:
        return alerts
    stop, label = effective_stop(p, m)
    if close <= stop:
        alerts.append(f"손절선 {stop:,.2f} 도달 ({label}) — 청산 규칙")

    mode = (p.get("trail") or {}).get("mode")
    if mode and mode.startswith("ma") and m.get(mode) is not None and close < m[mode]:
        alerts.append(f"{mode.upper()} 종가 이탈 — 추적 규칙상 정리")

    if m.get("topping_alert"):
        alerts.append("천정 패턴 발생 — 분할 청산 검토")
    if m.get("distribution_alert"):
        alerts.append("고점 대량거래 분산 경보 — 물량 손바뀜 의심")
    if m.get("three_black_crows"):
        alerts.append("흑삼병 — 하락 추세 전환 경계")

    entry0, stop0 = p.get("entry0"), p.get("stop0")
    if entry0 and stop0 and entry0 > stop0:
        r = (close - float(p.get("avg") or entry0)) / (entry0 - stop0)
        if r >= 2:
            alerts.append(f"+{r:.1f}R 도달 — 1차 익절 후 손절 본전 이동")
    return alerts


def main() -> None:
    positions = load_positions()
    if not positions:
        return
    state = load_state()
    sent = set(state.get("sent", []))

    us = [p["tick"] for p in positions if p.get("mkt") == "us"]
    kr = [p["tick"] for p in positions if p.get("mkt") == "kr"]
    metrics = {}
    if us:
        metrics.update({("us", k): v for k, v in fetch_us(us).items()})
    if kr:
        metrics.update({("kr", k): v for k, v in fetch_kr(kr).items()})

    lines, new = [], []
    for p in positions:
        m = metrics.get((p.get("mkt"), p.get("tick")))
        if not m:
            continue
        for a in check(p, m):
            token = f"{p['mkt']}:{p['tick']}:{a[:24]}"
            if token in sent:
                continue
            sent.add(token)
            new.append(token)
            lines.append(f"⚠️ <b>{p['tick']}</b> {a}\n    현재 {m['close']:,.2f}")

    if lines:
        now = dt.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%m-%d %H:%M")
        send_telegram(f"🚨 <b>포지션 경보</b> {now} KST\n" + "\n".join(lines))
        print(f"경보 {len(lines)}건 발송")
    else:
        print("경보 없음")

    state["sent"] = sorted(sent)
    save_state(state)


if __name__ == "__main__":
    main()
