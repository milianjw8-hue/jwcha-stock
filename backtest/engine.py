#!/usr/bin/env python3
"""백테스트 엔진 — 룩어헤드 없는 스윙 매매 시뮬레이션.

설계 원칙
1. 룩어헤드 금지. t일 종가까지의 데이터로만 판정하고 체결은 t+1일 시가로 한다.
   지표 계산에 넘기는 프레임은 항상 df.iloc[:t+1] 로 잘라서 준다.
2. 규칙은 docs/rules/*.json 을 그대로 읽는다. 백테스트용 규칙을 따로 두지 않는다.
   앱·스캐너와 다른 규칙을 시뮬레이션하면 검증의 의미가 없다.
3. 갭을 무시하지 않는다. 손절가 아래에서 갭 하락하면 시가에 체결된다.
4. 손익은 R 단위로 기록한다. 계좌 크기·수수료 가정에 결과가 좌우되지 않는다.

연구 모드(research=True)
  룰셋 통과 여부와 무관하게 사전필터를 통과한 모든 바에서 진입해 결과를 기록한다.
  항목별 판별력(lift)을 측정하려면 충족/미충족 양쪽 표본이 모두 필요하기 때문이다.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scanner"))
sys.path.insert(0, os.path.join(ROOT, "rules"))

import indicators as I  # noqa: E402
from rules import load_ruleset, score_ruleset  # noqa: E402


@dataclass
class Params:
    """스윕 대상 파라미터. 기본값은 docs/rules/exit_rules.json 을 따른다."""
    stop_pct: float = 5.0          # 초기 손절 (진입가 대비 %)
    first_target_r: float = 2.0    # 1차 익절 R
    first_sell_frac: float = 0.5   # 1차 익절 비중
    be_after_target: bool = True   # 1차 익절 후 손절선 본전 이동
    trail_mode: str = "ma20"       # ma5 | ma10 | ma20 | atr | pct | none
    trail_param: float = 2.5       # atr 배수 또는 pct
    max_hold: int = 120            # 최대 보유 거래일
    fail_breakout_days: int = 3    # 돌파 후 N일 내 피봇 이탈 = 실패 돌파
    max_open: int = 8              # 동시 보유 종목 수
    cooldown: int = 10             # 청산 후 같은 종목 재진입 금지 기간
    gate_pct_of_high: float = 75.0  # 사전필터 — 52주 고점 대비
    gate_vol_ratio: float = 1.5     # 사전필터 — 거래량 배수
    require_market_ok: bool = True  # 시장 필터 미통과 시 신규 진입 금지


@dataclass
class Trade:
    market: str
    ticker: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    stop0: float
    r: float
    pct: float
    bars: int
    reason: str
    rule_pct: int
    verdict: str
    items: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)


# ── 값싼 벡터 사전필터 ────────────────────────────────────────────────
def cheap(df: pd.DataFrame) -> dict:
    c, h, v = df["close"], df["high"], df["volume"]
    vol_ma = v.shift(1).rolling(20).mean()
    hi52 = h.rolling(252, min_periods=60).max()
    lo52 = df["low"].rolling(252, min_periods=60).min()
    return {
        "pct_of_high": (c / hi52 * 100).to_numpy(),
        "pct_above_low": ((c / lo52 - 1) * 100).to_numpy(),
        "vol_ratio": (v / vol_ma).to_numpy(),
        "ma20": c.rolling(20).mean().to_numpy(),
        "ma10": c.rolling(10).mean().to_numpy(),
        "ma5": c.rolling(5).mean().to_numpy(),
        "wret": weighted_return_series(c),
    }


def weighted_return_series(close: pd.Series) -> np.ndarray:
    """IBD식 가중 수익률 — 최근 3개월에 2배 가중. RS 백분위의 재료."""
    def r(n):
        return (close / close.shift(n) - 1).to_numpy()
    r3, r6, r9, r12 = r(63), r(126), r(189), r(252)
    parts = np.vstack([2 * r3, r6, r9, r12])
    out = np.nansum(parts, axis=0)
    out[np.isnan(r3)] = np.nan     # 3개월치도 없으면 판정 불가
    return out


def atr_series(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(),
                    (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().to_numpy()


# ── 시뮬레이션 ────────────────────────────────────────────────────────
class Backtest:
    def __init__(self, market: str, frames: dict[str, pd.DataFrame],
                 index_df: pd.DataFrame | None, params: Params, research: bool = False):
        self.market = market
        self.params = params
        self.research = research
        self.ruleset = load_ruleset("us_minervini" if market == "us" else "kr_leader")

        self.frames, self.pos_of, self.cheap, self.atr = {}, {}, {}, {}
        for t, df in frames.items():
            df = I.normalize(df)
            if len(df) < 260:
                continue
            self.frames[t] = df
            self.pos_of[t] = {d: i for i, d in enumerate(df.index)}
            self.cheap[t] = cheap(df)
            self.atr[t] = atr_series(df)

        self.dates = sorted({d for df in self.frames.values() for d in df.index})
        self.market_ok = self._market_filter(index_df)

    def _market_filter(self, idx: pd.DataFrame | None) -> dict:
        """지수 필터 — 미장 SPY 종가>50>200, 국장 지수 종가>20일선·60일선."""
        if idx is None or idx.empty:
            return {}
        c = idx["close"] if "close" in idx.columns else idx.iloc[:, 0]
        if self.market == "us":
            ok = (c > c.rolling(50).mean()) & (c.rolling(50).mean() > c.rolling(200).mean())
        else:
            ok = (c > c.rolling(20).mean()) & (c > c.rolling(60).mean())
        return {d: bool(x) for d, x in ok.items() if not pd.isna(x)}

    def _rs_pctile(self, d) -> dict[str, float]:
        """해당 날짜의 유니버스 횡단면 RS 백분위. 미래 데이터를 쓰지 않는다."""
        vals = []
        for t, ch in self.cheap.items():
            i = self.pos_of[t].get(d)
            if i is None:
                continue
            w = ch["wret"][i]
            if not np.isnan(w):
                vals.append((t, w))
        if len(vals) < 20:
            return {}
        order = sorted(vals, key=lambda x: x[1])
        n = len(order)
        return {t: (rank + 1) / n * 100 for rank, (t, _) in enumerate(order)}

    # ── 1단계: 진입 신호 수집 (무겁다 — 한 번만 실행하고 재사용) ──────
    def collect_signals(self) -> list[dict]:
        """t일 종가 기준 진입 신호. 지표 계산에는 df.iloc[:t+1] 만 넘긴다."""
        p = self.params
        out: list[dict] = []
        for d in self.dates:
            if p.require_market_ok and not self.market_ok.get(d, True):
                continue
            rs = self._rs_pctile(d) if self.market == "us" else {}
            for t, df in self.frames.items():
                i = self.pos_of[t].get(d)
                if i is None or i < 260 or i + 1 >= len(df):
                    continue
                ch = self.cheap[t]
                if not (ch["pct_of_high"][i] >= p.gate_pct_of_high
                        and ch["vol_ratio"][i] >= p.gate_vol_ratio):
                    continue

                extra = {"market_ok": bool(self.market_ok.get(d, True))}
                if self.market == "us":
                    if t not in rs:
                        continue
                    extra["rs_pctile"] = rs[t]
                # 국장 수급 지표(거래대금 상위 유지일수·연속 순매수)는 과거 재현 비용이
                # 커서 백테스트에서는 판정 불가로 둔다. 해당 항목이 자동 충족되지 않으므로
                # 국장 룰셋 통과 문턱은 실전보다 높다 — 결과를 보수적으로 읽어야 한다.

                m = I.compute(df.iloc[:i + 1], market=self.market, extra=extra)
                if not m:
                    continue
                sc = score_ruleset(self.ruleset, m)
                if not self.research and sc["verdict"] != "진입 가능":
                    continue
                out.append({
                    "date": d, "ticker": t, "metrics": m,
                    "rule_pct": sc["pct"], "verdict": sc["verdict"],
                    "items": {x["id"]: x["ok"] for x in sc["items"] if x["src"] == "auto"},
                })
        return out

    # ── 2단계: 진입/청산 시뮬레이션 (가볍다 — 파라미터 스윕용) ────────
    def simulate(self, signals: list[dict], params: Params | None = None,
                 accept=None) -> list[Trade]:
        """accept(sig) -> bool 로 신호를 추가 필터할 수 있다 (가중치 검증용)."""
        p = params or self.params
        by_date: dict = {}
        for s in signals:
            if accept is None or accept(s):
                by_date.setdefault(s["date"], []).append(s)

        trades: list[Trade] = []
        open_pos: dict[str, dict] = {}
        pending: list[dict] = []
        cooldown: dict[str, int] = {}

        for di, d in enumerate(self.dates):
            # 1) 보유 포지션을 당일 바로 관리
            for t in list(open_pos.keys()):
                i = self.pos_of[t].get(d)
                if i is None or i <= open_pos[t]["i0"]:
                    continue
                tr = self._bar(t, d, i, open_pos[t], p)
                if tr:
                    trades.append(tr)
                    cooldown[t] = di + p.cooldown
                    del open_pos[t]

            # 2) 전일 신호를 당일 시가에 체결
            for sig in pending:
                t = sig["ticker"]
                if t in open_pos or len(open_pos) >= p.max_open or cooldown.get(t, -1) > di:
                    continue
                i = self.pos_of[t].get(d)
                if i is None:
                    continue
                entry = float(self.frames[t]["open"].iloc[i])
                if entry <= 0:
                    continue
                stop0 = entry * (1 - p.stop_pct / 100)
                st = {
                    "entry": entry, "stop0": stop0, "stop": stop0, "i0": i,
                    "date0": str(d)[:10], "high": entry, "qty": 1.0, "realized": 0.0,
                    "took_profit": False, "pivot": sig["metrics"].get("pivot"),
                    "rule_pct": sig["rule_pct"], "verdict": sig["verdict"],
                    "items": sig["items"], "metrics": sig["metrics"],
                }
                # 진입 당일의 남은 장중 움직임도 검사한다. 진입 후 같은 날 손절선이
                # 깨지는 경우를 빼면 성과가 낙관 쪽으로 치우친다.
                open_pos[t] = st
                tr = self._bar(t, d, i, st, p, entry_bar=True)
                if tr:
                    trades.append(tr)
                    cooldown[t] = di + p.cooldown
                    del open_pos[t]
            pending = by_date.get(d, [])

        # 기간 종료 시 미청산 포지션은 마지막 종가로 정리
        for t, st in open_pos.items():
            df = self.frames[t]
            trades.append(self._close(t, st, float(df["close"].iloc[-1]),
                                      str(df.index[-1])[:10], len(df) - 1, "기간종료"))
        trades.sort(key=lambda x: x.exit_date)
        return trades

    def run(self) -> list[Trade]:
        return self.simulate(self.collect_signals())

    def _bar(self, t: str, d, i: int, st: dict, p: Params,
             entry_bar: bool = False) -> Trade | None:
        """보유 중 한 바 진행. 청산되면 Trade 를 돌려준다."""
        df = self.frames[t]
        o = float(df["open"].iloc[i]); hi = float(df["high"].iloc[i])
        lo = float(df["low"].iloc[i]); c = float(df["close"].iloc[i])
        st["high"] = max(st["high"], hi)
        risk0 = st["entry"] - st["stop0"]

        # 손절 — 갭 하락이면 시가 체결
        if lo <= st["stop"]:
            px = min(o, st["stop"])
            return self._close(t, st, px, str(d)[:10], i, "손절")

        # 1차 익절 — 절반 정리 후 손절선 본전 이동
        target = st["entry"] + risk0 * p.first_target_r
        if not st["took_profit"] and hi >= target and p.first_sell_frac > 0:
            frac = p.first_sell_frac
            st["realized"] += frac * (target - st["entry"]) / risk0
            st["qty"] -= frac
            st["took_profit"] = True
            if p.be_after_target:
                st["stop"] = max(st["stop"], st["entry"])

        # 진입 당일은 손절·1차 익절만 본다. 추적 규칙은 최소 한 바를 보유한 뒤부터
        # 적용해야 진입 직후 노이즈에 털리지 않는다.
        if entry_bar:
            return None

        # 실패 돌파 — 돌파 후 N일 내 피봇 아래 종가
        if st.get("pivot") and (i - st["i0"]) <= p.fail_breakout_days and c < st["pivot"]:
            return self._close(t, st, c, str(d)[:10], i, "실패돌파")

        # 트레일링 — 한 번 올린 손절선은 내리지 않는다
        ch = self.cheap[t]
        trail = None
        if p.trail_mode in ("ma5", "ma10", "ma20"):
            mv = ch[p.trail_mode][i]
            if not np.isnan(mv) and c < mv:
                return self._close(t, st, c, str(d)[:10], i, p.trail_mode + " 이탈")
        elif p.trail_mode == "atr":
            a = self.atr[t][i]
            if not np.isnan(a):
                trail = st["high"] - a * p.trail_param
        elif p.trail_mode == "pct":
            trail = st["high"] * (1 - p.trail_param / 100)
        if trail is not None:
            st["stop"] = max(st["stop"], trail)

        if (i - st["i0"]) >= p.max_hold:
            return self._close(t, st, c, str(d)[:10], i, "보유기간 만료")
        return None

    def _close(self, t: str, st: dict, px: float, date: str, i: int, reason: str) -> Trade:
        risk0 = st["entry"] - st["stop0"]
        r = st["realized"] + st["qty"] * (px - st["entry"]) / risk0 if risk0 > 0 else 0.0
        return Trade(
            market=self.market, ticker=t, entry_date=st["date0"], exit_date=date,
            entry=round(st["entry"], 4), exit=round(px, 4), stop0=round(st["stop0"], 4),
            r=round(r, 3), pct=round((px / st["entry"] - 1) * 100, 2),
            bars=i - st["i0"], reason=reason,
            rule_pct=st["rule_pct"], verdict=st["verdict"],
            items=st["items"], metrics={k: st["metrics"].get(k) for k in (
                "bb_squeeze", "band_state", "cup_handle", "double_bottom", "vcp_shrinking",
                "breakout_60", "vol_ratio_20", "pct_of_52wk_high", "ma_aligned_bull",
                "distribution_alert", "three_white_soldiers", "retest_level")},
        )
