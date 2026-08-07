#!/usr/bin/env python3
"""지표 엔진 — 차트 개념을 계산 가능한 숫자로 변환한다.

영상 자료의 4대 기본 요소(캔들·이평선·거래량·지지저항)와 볼린저 밴드를
`rules/*.json`이 참조할 수 있는 metrics 딕셔너리로 산출한다.

입력은 open/high/low/close/volume 컬럼을 가진 pandas DataFrame(과거→현재 정렬).
출력값은 JSON 직렬화 가능한 파이썬 기본형만 사용한다. 판정 불가한 지표는 None.
"""
from __future__ import annotations

import math

import pandas as pd

# ── 상수 ──────────────────────────────────────────────────────────────
YEAR_BARS = 252
VOL_MA = 20
BB_LEN, BB_STD = 20, 2.0
ATR_LEN = 14
SQUEEZE_LOOKBACK = 126     # 반년 — 밴드폭 백분위 기준
SQUEEZE_PCTILE = 20        # 밴드폭 백분위 하위 20% 이면 스퀴즈
BREAKOUT_LOOKBACK = 60
PIVOT_LOOKBACK = 20
DISPARITY_LOOKBACK = 504   # 2년 — 이격도 백분위 기준

# 거래량 검증 문턱. 이 아래면 어떤 신호도 신뢰하지 않는다(영상 19:12).
VOL_CONFIRM = 1.5


def _f(x) -> float | None:
    """NaN/inf 를 None 으로 바꿔 JSON 안전하게 만든다."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _r(x, nd=2) -> float | None:
    v = _f(x)
    return None if v is None else round(v, nd)


def normalize(df: pd.DataFrame, cols: dict[str, str] | None = None) -> pd.DataFrame:
    """컬럼명을 open/high/low/close/volume 으로 통일하고 결측을 제거한다."""
    if cols:
        df = df.rename(columns=cols)
    df = df.rename(columns={c: str(c).lower() for c in df.columns})
    need = ["open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")
    out = df[need].apply(pd.to_numeric, errors="coerce").dropna()
    return out[out["close"] > 0]


def pctile_of(series: pd.Series, value: float) -> float | None:
    """series 안에서 value 가 놓인 백분위(0~100)."""
    s = series.dropna()
    if len(s) < 20 or value is None:
        return None
    return float((s <= value).mean() * 100)


def atr(df: pd.DataFrame, n: int = ATR_LEN) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def cross_days(fast: pd.Series, slow: pd.Series, up: bool, limit: int = 250) -> int | None:
    """마지막 골든(up=True)/데드 크로스로부터 경과한 거래일 수. 없으면 None."""
    d = (fast - slow).dropna()
    if len(d) < 3:
        return None
    sign = d > 0
    for i in range(len(d) - 1, 0, -1):
        crossed = (sign.iloc[i] and not sign.iloc[i - 1]) if up else (not sign.iloc[i] and sign.iloc[i - 1])
        if crossed:
            age = len(d) - 1 - i
            return age if age <= limit else None
    return None


def swing_points(df: pd.DataFrame, k: int = 4) -> tuple[list[int], list[int]]:
    """프랙탈 스윙 고점/저점 인덱스(위치 기준)."""
    hi, lo = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(hi)
    highs, lows = [], []
    for i in range(k, n - k):
        w_h, w_l = hi[i - k:i + k + 1], lo[i - k:i + k + 1]
        if hi[i] == w_h.max() and (w_h.argmax() == k):
            highs.append(i)
        if lo[i] == w_l.min() and (w_l.argmin() == k):
            lows.append(i)
    return highs, lows


def sr_levels(df: pd.DataFrame, lookback: int = 250, tol: float = 0.015,
              min_touch: int = 3, top: int = 6) -> list[dict]:
    """지지·저항선 — 스윙 고저점을 ±1.5% 로 묶어 3회 이상 접촉한 밴드만 남긴다."""
    d = df.tail(lookback)
    if len(d) < 40:
        return []
    highs, lows = swing_points(d)
    pts = [float(d["high"].iloc[i]) for i in highs] + [float(d["low"].iloc[i]) for i in lows]
    if not pts:
        return []
    pts.sort()

    clusters: list[list[float]] = []
    for p in pts:
        if clusters and p <= clusters[-1][-1] * (1 + tol):
            clusters[-1].append(p)
        else:
            clusters.append([p])

    close = float(d["close"].iloc[-1])
    out = [
        {
            "price": round(sum(c) / len(c), 4),
            "touches": len(c),
            "kind": "resistance" if sum(c) / len(c) > close else "support",
        }
        for c in clusters if len(c) >= min_touch
    ]
    out.sort(key=lambda r: abs(r["price"] - close))
    return out[:top]


def retest_level(df: pd.DataFrame, levels: list[dict], within: int = 10,
                 tol: float = 0.02) -> float | None:
    """역할 전환(저항→지지) 리테스트 — 최근 돌파한 저항선에 되돌림 접촉 중인가."""
    if len(df) < within + 2 or not levels:
        return None
    close = float(df["close"].iloc[-1])
    low = float(df["low"].iloc[-1])
    past_close = df["close"].iloc[-(within + 1):-1]
    for lv in levels:
        p = lv["price"]
        # 최근 within 일 안에 아래→위로 넘어섰고, 지금은 그 선 근처로 되돌아왔다
        if close > p and (past_close < p).any() and abs(low - p) / p <= tol:
            return round(p, 4)
    return None


def compute(df: pd.DataFrame, market: str = "us", extra: dict | None = None) -> dict:
    """metrics 딕셔너리 산출. df 는 normalize() 를 거친 것이어야 한다."""
    from patterns import detect_patterns  # 순환 참조 회피 (같은 디렉터리)

    m: dict = dict(extra or {})
    n = len(df)
    if n < 60:
        return m

    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    close = float(c.iloc[-1])
    m["close"] = _r(close, 4)
    m["open"] = _r(o.iloc[-1], 4)
    m["high"] = _r(h.iloc[-1], 4)
    m["low"] = _r(l.iloc[-1], 4)
    m["volume"] = _f(v.iloc[-1])
    m["bars"] = n

    # ── 이동평균선 ────────────────────────────────────────────────
    mas = {}
    for p in (5, 10, 20, 50, 60, 120, 150, 200):
        mas[p] = c.rolling(p).mean() if n >= p else None
        m[f"ma{p}"] = _r(mas[p].iloc[-1], 4) if mas[p] is not None else None

    def ma_at(p, back=0):
        s = mas.get(p)
        if s is None or len(s) <= back:
            return None
        return _f(s.iloc[-1 - back])

    # 200일선 기울기 = 한 달(20거래일) 변화율
    for p, span in ((200, 20), (150, 20), (20, 5), (60, 20), (120, 20)):
        a, b = ma_at(p), ma_at(p, span)
        m[f"ma{p}_slope_{span}d_pct"] = _r((a / b - 1) * 100, 3) if a and b else None

    m["close_vs_ma20_pct"] = _r((close / m["ma20"] - 1) * 100, 2) if m["ma20"] else None

    us_bull = all(m[k] is not None for k in ("ma50", "ma150", "ma200")) and \
        m["ma50"] > m["ma150"] > m["ma200"]
    kr_bull = all(m[k] is not None for k in ("ma5", "ma20", "ma60", "ma120")) and \
        m["ma5"] > m["ma20"] > m["ma60"] > m["ma120"]
    us_bear = all(m[k] is not None for k in ("ma50", "ma150", "ma200")) and \
        m["ma50"] < m["ma150"] < m["ma200"]
    kr_bear = all(m[k] is not None for k in ("ma5", "ma20", "ma60", "ma120")) and \
        m["ma5"] < m["ma20"] < m["ma60"] < m["ma120"]
    m["ma_aligned_bull"] = kr_bull if market == "kr" else us_bull
    m["ma_aligned_bear"] = kr_bear if market == "kr" else us_bear

    # 골든/데드 크로스 — 국장은 20/60, 미장은 50/200
    fast_p, slow_p = (20, 60) if market == "kr" else (50, 200)
    if mas[fast_p] is not None and mas[slow_p] is not None:
        m["golden_cross_days"] = cross_days(mas[fast_p], mas[slow_p], up=True)
        m["dead_cross_days"] = cross_days(mas[fast_p], mas[slow_p], up=False)

    # ── 이격도 (영상 16:43) ───────────────────────────────────────
    if m["ma20"]:
        disp = close / m["ma20"] * 100
        m["disparity20"] = _r(disp, 2)
        hist = (c / mas[20] * 100).tail(DISPARITY_LOOKBACK)
        m["disparity20_pctile"] = _r(pctile_of(hist, disp), 1)

    # ── 거래량 (영상 18:54) ───────────────────────────────────────
    vol_ma20 = float(v.iloc[-(VOL_MA + 1):-1].mean()) if n > VOL_MA else 0.0
    m["vol_ma20"] = _r(vol_ma20, 1)
    m["vol_ratio_20"] = _r(v.iloc[-1] / vol_ma20, 2) if vol_ma20 > 0 else None
    m["vol_confirmed"] = (m["vol_ratio_20"] is not None and m["vol_ratio_20"] >= VOL_CONFIRM)

    # ── 52주 위치 ─────────────────────────────────────────────────
    win = df.tail(YEAR_BARS)
    hi52, lo52 = float(win["high"].max()), float(win["low"].min())
    m["high_52wk"], m["low_52wk"] = _r(hi52, 4), _r(lo52, 4)
    m["pct_of_52wk_high"] = _r(close / hi52 * 100, 1) if hi52 > 0 else None
    m["pct_above_52wk_low"] = _r((close / lo52 - 1) * 100, 1) if lo52 > 0 else None

    # ── 변동성 · 볼린저 (영상 01:34:33) ───────────────────────────
    a = atr(df)
    m["atr14"] = _r(a.iloc[-1], 4)
    m["atr_pct"] = _r(a.iloc[-1] / close * 100, 2) if m["atr14"] else None

    if n >= BB_LEN:
        mid = c.rolling(BB_LEN).mean()
        sd = c.rolling(BB_LEN).std(ddof=0)
        up, dn = mid + BB_STD * sd, mid - BB_STD * sd
        width = (up - dn) / mid
        m["bb_upper"], m["bb_mid"], m["bb_lower"] = _r(up.iloc[-1], 4), _r(mid.iloc[-1], 4), _r(dn.iloc[-1], 4)
        rng = _f(up.iloc[-1] - dn.iloc[-1])
        m["bb_pctb"] = _r((close - dn.iloc[-1]) / rng, 3) if rng else None
        m["bb_width"] = _r(width.iloc[-1], 4)
        w_hist = width.tail(SQUEEZE_LOOKBACK).dropna()
        m["bb_width_pctile"] = _r(pctile_of(w_hist, _f(width.iloc[-1])), 1)
        # 스퀴즈 판정 (영상 01:41:06) — 두 조건 중 하나면 인정한다.
        #  1) 밴드폭 백분위가 하위 20% (일반적인 변동성 수축)
        #  2) 밴드폭이 반년 중앙값의 절반 이하 (저변동성이 오래 지속되는 국면)
        # 백분위만 쓰면 스퀴즈가 길어질수록 그 구간 자체가 분모가 되어 정작 가장
        # 좁은 국면에서 백분위가 올라가 버린다. 중앙값 조건이 그 맹점을 덮는다.
        w_now = _f(width.iloc[-1])
        w_med = float(w_hist.median()) if len(w_hist) else 0.0
        m["bb_squeeze"] = bool(
            len(w_hist) >= 40 and w_now is not None and (
                (m["bb_width_pctile"] is not None and m["bb_width_pctile"] <= SQUEEZE_PCTILE)
                or (w_med > 0 and w_now <= w_med * 0.5)
            )
        )

        # 밴드 워킹 판정 (영상 01:40:21) — 상단 접촉을 과매수로 오인하는 실수를 막는다
        state = "none"
        if m["bb_pctb"] is not None and m["bb_pctb"] >= 0.90:
            slope_ok = (m.get("ma20_slope_5d_pct") or 0) > 0
            widening = len(width) > 5 and _f(width.iloc[-1]) > _f(width.iloc[-6])
            state = "walk" if (slope_ok and widening) else "warn"
        elif m["bb_pctb"] is not None and m["bb_pctb"] <= 0.05:
            state = "lower"
        m["band_state"] = state

    # ── 캔들 (영상 02:10) ─────────────────────────────────────────
    rng_today = float(h.iloc[-1] - l.iloc[-1])
    if rng_today > 0:
        body_hi = max(float(o.iloc[-1]), close)
        body_lo = min(float(o.iloc[-1]), close)
        m["upper_wick_ratio"] = _r((float(h.iloc[-1]) - body_hi) / rng_today, 3)
        m["lower_wick_ratio"] = _r((body_lo - float(l.iloc[-1])) / rng_today, 3)
        m["body_ratio"] = _r(abs(close - float(o.iloc[-1])) / rng_today, 3)
    m["is_bullish_candle"] = bool(close > float(o.iloc[-1]))
    m["day_range_pct"] = _r(rng_today / close * 100, 2) if close else None

    # ── 돌파 · 피봇 ───────────────────────────────────────────────
    if n > BREAKOUT_LOOKBACK:
        prior = float(h.iloc[-(BREAKOUT_LOOKBACK + 1):-1].max())
        m["prior_high_60"] = _r(prior, 4)
        m["breakout_60"] = bool(close > prior)
    if n > PIVOT_LOOKBACK:
        pivot = float(h.iloc[-(PIVOT_LOOKBACK + 1):-1].max())
        m["pivot"] = _r(pivot, 4)
        m["dist_to_pivot_pct"] = _r((pivot / close - 1) * 100, 2)

    # 20일선 눌림 지지 후 반등 (국장 트리거)
    if mas[20] is not None and n >= 25:
        recent = df.tail(5)
        ma20_recent = mas[20].tail(5)
        touched = bool(((recent["low"] - ma20_recent).abs() / ma20_recent <= 0.02).any())
        m["ma20_pullback_bounce"] = bool(
            touched and close > float(mas[20].iloc[-1]) and close > float(c.iloc[-2])
        )

    # ── 지지·저항 (영상 21:57) ────────────────────────────────────
    levels = sr_levels(df)
    m["sr_levels"] = levels
    m["retest_level"] = retest_level(df, levels)

    # ── 고점 분산 경보 (영상 20:39) ───────────────────────────────
    m["distribution_alert"] = bool(
        (m.get("pct_of_52wk_high") or 0) >= 90
        and (m.get("vol_ratio_20") or 0) >= 2.0
        and (m.get("upper_wick_ratio") or 0) >= 0.40
        and (m.get("atr14") and rng_today > float(a.iloc[-1]) * 1.5)
    )

    # ── 패턴 ──────────────────────────────────────────────────────
    m.update(detect_patterns(df))
    return m


def ohlc_payload(df: pd.DataFrame, bars: int = 140) -> dict:
    """차트 탭이 그릴 최소 페이로드. 배열 형태로 압축한다."""
    d = df.tail(bars)
    idx = d.index
    dates = [str(x)[:10] if not hasattr(x, "strftime") else x.strftime("%Y-%m-%d") for x in idx]
    return {
        "d": dates,
        "o": [_r(x, 4) for x in d["open"]],
        "h": [_r(x, 4) for x in d["high"]],
        "l": [_r(x, 4) for x in d["low"]],
        "c": [_r(x, 4) for x in d["close"]],
        "v": [_f(x) for x in d["volume"]],
    }
