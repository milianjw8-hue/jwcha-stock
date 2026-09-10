#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""세븐스플릿 x 스윙 통합 (코어+새틀라이트) 백테스트 — split_engine.py 와 동일 규칙.

  코어      : core_frac 비중 첫날 전액 매수, 영구 보유 (장기투자 축)
  새틀라이트 : 6회차 피라미드(1.0~1.6). 앵커*(1-k*gap) 레벨 + 반등일에 매수
  국면      : 종가<200일선이면 gap x1.5
  매도      : 1~3회차 고정 익절 / 4~6회차 목표 도달 후 20일선 이탈 추적
검증 결과(2013~2026.9): 코어0.6 기준 VOO 11.6%/-29.4%, QQQ 16.1%/-31.9%,
SCHD 10.3%/-28.8% (BH 대비 CAGR -3~4%p, MDD -3~5%p 개선). 순수 스플릿(코어0)은
CAGR 4~6%로 부적합 — 강세장 현금 드래그 때문. 파라미터 변경 시 재검증할 것.
"""
import numpy as np
import pandas as pd
import yfinance as yf

ASSETS = {
    "VOO":   dict(gap=.035, target=.045, core=0.6, anchor="high"),
    "QQQ":   dict(gap=.050, target=.065, core=0.6, anchor="high"),
    "SCHD":  dict(gap=.028, target=.035, core=0.6, anchor="high"),
    "KRW=X": dict(gap=.012, target=.015, core=0.0, anchor="mean"),
}
N, PYR = 6, [1.0, 1.1, 1.2, 1.3, 1.45, 1.6]
START = "2013-01-01"


def run(sym, p):
    df = yf.download(sym, start=START, progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    c = df["Close"].dropna()
    ma200, ma20 = c.rolling(200).mean(), c.rolling(20).mean()
    anchor = c.rolling(1260, min_periods=252).mean() if p["anchor"] == "mean" \
             else c.rolling(252, min_periods=60).max()
    core_sh = p["core"] / float(c.iloc[0])
    cash = 1.0 - p["core"]
    unit = cash / sum(PYR)
    tr = [None] * N
    eq = []
    for i in range(len(c)):
        px = float(c.iloc[i]); a = float(anchor.iloc[i])
        below = bool(px < ma200.iloc[i]) if not np.isnan(ma200.iloc[i]) else False
        g = p["gap"] * (1.5 if below else 1.0)
        up = i > 0 and px > float(c.iloc[i - 1])
        for k in range(N):
            t = tr[k]
            if not t:
                continue
            hit = px >= t["buy"] * (1 + p["target"])
            if k < 3:
                if hit:
                    cash += t["sh"] * px; tr[k] = None
            else:
                if hit:
                    t["trail"] = True
                if t.get("trail") and px < float(ma20.iloc[i]):
                    cash += t["sh"] * px; tr[k] = None
        for k in range(N):
            if tr[k] is None:
                if px <= a * (1 - (k + 1) * g) and up:
                    amt = unit * PYR[k]
                    if cash >= amt * 0.999:
                        cash -= amt; tr[k] = {"sh": amt / px, "buy": px}
                break
        eq.append(cash + core_sh * px + sum(t["sh"] * px for t in tr if t))
    eq = pd.Series(eq, index=c.index)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    bh = c / c.iloc[0]
    return dict(
        cagr=round((float(eq.iloc[-1]) ** (1 / yrs) - 1) * 100, 2),
        mdd=round(float((eq / eq.cummax() - 1).min()) * 100, 1),
        bh_cagr=round((float(bh.iloc[-1]) ** (1 / yrs) - 1) * 100, 2),
        bh_mdd=round(float((bh / bh.cummax() - 1).min()) * 100, 1),
    )


if __name__ == "__main__":
    print(f"{'자산':8}{'CAGR':>8}{'MDD':>8}   (단순보유 CAGR/MDD)")
    for sym, p in ASSETS.items():
        r = run(sym, p)
        print(f"{sym:8}{r['cagr']:>7.2f}%{r['mdd']:>7.1f}%   ({r['bh_cagr']}% / {r['bh_mdd']}%)")
