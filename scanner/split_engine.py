#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스플릿 엔진 — 코어+새틀라이트 세븐스플릿의 일일 레벨/신호 계산.

jwcha-stock 저장소용. docs/split.json 으로 저장하며, docs/split.html 이 읽는다.

구조 (백테스트 split_backtest.py 와 동일 규칙):
  코어    : 자산별 core_frac 비중은 상시 보유 (엔진은 관여하지 않음)
  새틀라이트: 6회차 피라미드(1.0~1.6배). 앵커 대비 -k*gap 레벨에서 분할매수
  앵커    : ETF = 252일 최고가 / USD = 1260일 평균
  국면    : 종가 < 200일선이면 gap x1.5 (레벨 심화 = 실탄 보존)
  매수신호: 레벨 이하 도달 + 당일 양전(반등일) → 텔레그램 알림
  매도    : 회차별 목표는 사용자의 실제 매수가 기준이므로 split.html 의
            장부(localStorage)가 계산한다. 엔진은 레벨과 국면만 제공.

실행: python scanner/split_engine.py   (Actions: .github/workflows/split.yml)
"""
import datetime as dt
import json
import os
import sys

import pandas as pd
import yfinance as yf

try:
    from telegram_push import send_telegram          # 저장소 헬퍼
except Exception:                                     # 단독 실행 대비
    def send_telegram(text):
        print("[텔레그램 생략]\n" + text); return False

OUT = os.environ.get("OUT_PATH", "docs/split.json")

ASSETS = {
    "VOO":   dict(label="VOO",  gap=0.035, target=0.045, core_frac=0.6, anchor="high"),
    "QQQ":   dict(label="QQQ",  gap=0.050, target=0.065, core_frac=0.6, anchor="high"),
    "SCHD":  dict(label="SCHD", gap=0.028, target=0.035, core_frac=0.6, anchor="high"),
    "KRW=X": dict(label="USD/KRW", gap=0.012, target=0.015, core_frac=0.0, anchor="mean"),
}
N_TRANCHES = 6
PYRAMID = [1.0, 1.1, 1.2, 1.3, 1.45, 1.6]


def analyze(sym, p):
    df = yf.download(sym, period="6y", interval="1d", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    c = df["Close"].dropna()
    if len(c) < 260:
        return None
    close = float(c.iloc[-1])
    prev = float(c.iloc[-2])
    ma200 = float(c.rolling(200).mean().iloc[-1])
    ma20 = float(c.rolling(20).mean().iloc[-1])
    anchor = float(c.rolling(1260, min_periods=252).mean().iloc[-1]) \
        if p["anchor"] == "mean" else float(c.tail(252).max())

    below200 = close < ma200
    g = p["gap"] * (1.5 if below200 else 1.0)
    up_day = close > prev

    levels = []
    active_k = 0                      # 현재 종가가 도달한 가장 깊은 회차
    for k in range(1, N_TRANCHES + 1):
        lvl = anchor * (1 - k * g)
        reached = close <= lvl
        if reached:
            active_k = k
        levels.append({"k": k, "price": round(lvl, 4), "reached": bool(reached),
                       "weight": PYRAMID[k - 1]})

    buy_signal = bool(active_k >= 1 and up_day)
    return {
        "symbol": sym, "label": p["label"],
        "close": round(close, 4), "chg_pct": round((close / prev - 1) * 100, 2),
        "anchor": round(anchor, 4), "anchor_type": p["anchor"],
        "ma20": round(ma20, 4), "ma200": round(ma200, 4),
        "below_ma200": bool(below200), "gap_effective": round(g, 4),
        "up_day": bool(up_day), "active_k": active_k,
        "buy_signal": buy_signal,
        "target_pct": p["target"], "core_frac": p["core_frac"],
        "levels": levels,
    }


def main():
    assets, alerts = [], []
    for sym, p in ASSETS.items():
        try:
            a = analyze(sym, p)
        except Exception as e:
            print(f"{sym} 실패: {e}", file=sys.stderr); a = None
        if not a:
            continue
        assets.append(a)
        if a["buy_signal"]:
            alerts.append(f"🟢 {a['label']} {a['active_k']}회차 레벨 도달 + 반등일 "
                          f"(종가 {a['close']}, 앵커대비 "
                          f"{round((a['close']/a['anchor']-1)*100,1)}%)")
        elif a["active_k"] >= 1:
            alerts.append(f"🟡 {a['label']} {a['active_k']}회차 구간 (반등 대기)")

    out = {
        "asof": dt.datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        "n_tranches": N_TRANCHES, "pyramid": PYRAMID,
        "assets": assets,
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"saved {OUT}: {len(assets)} assets")

    if alerts:
        send_telegram("[스플릿] " + out["asof"] + "\n" + "\n".join(alerts))


if __name__ == "__main__":
    main()
