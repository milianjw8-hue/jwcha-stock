"""Detect active rising/falling wedge candidates from daily OHLCV bars.

The result is a watchlist clue, never an entry/exit instruction. The pattern
uses confirmed local swing highs/lows, converging fitted boundaries and
volume contraction. A boundary break is marked separately from confirmation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


WINDOWS = (30, 40, 50, 60)
MIN_CONTRACTION = 0.15
MAX_CONTRACTION = 0.85
PIVOT_TOLERANCE = 0.035
VOLUME_DRYUP = 0.85
BREAK_VOLUME = 1.30


def _swings(values: np.ndarray, k: int = 2) -> tuple[list[int], list[int]]:
    highs, lows = [], []
    for i in range(k, len(values) - k):
        high_window = values[i-k:i+k+1, 0]
        low_window = values[i-k:i+k+1, 1]
        if values[i, 0] == high_window.max() and high_window.argmax() == k:
            highs.append(i)
        if values[i, 1] == low_window.min() and low_window.argmin() == k:
            lows.append(i)
    return highs, lows


def _fit(points: list[int], prices: pd.Series) -> tuple[float, float]:
    return tuple(float(x) for x in np.polyfit(points, [float(prices.iloc[i]) for i in points], 1))


def detect_wedge(frame: pd.DataFrame) -> dict | None:
    """Return the strongest active wedge in a normalized OHLCV dataframe."""
    if frame is None or len(frame) < max(WINDOWS):
        return None
    d = frame.rename(columns={str(c): str(c).lower() for c in frame.columns})
    if not set(("high", "low", "close", "volume")).issubset(d.columns):
        return None
    d = d[["high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").dropna().tail(90).reset_index(drop=True)
    if len(d) < max(WINDOWS):
        return None

    candidates = []
    for size in WINDOWS:
        w = d.tail(size).reset_index(drop=True)
        vals = w[["high", "low"]].to_numpy(dtype=float)
        hi_pts, lo_pts = _swings(vals, k=2)
        if len(hi_pts) < 2 or len(lo_pts) < 2:
            continue
        hi_pts, lo_pts = hi_pts[-4:], lo_pts[-4:]
        up_slope, up_intercept = _fit(hi_pts, w["high"])
        low_slope, low_intercept = _fit(lo_pts, w["low"])
        x0, x1 = 0, size - 1
        upper0, upper1 = up_intercept, up_slope * x1 + up_intercept
        lower0, lower1 = low_intercept, low_slope * x1 + low_intercept
        width0, width1 = upper0 - lower0, upper1 - lower1
        if width0 <= 0 or width1 <= 0:
            continue
        contraction = 1 - width1 / width0
        if not MIN_CONTRACTION <= contraction <= MAX_CONTRACTION:
            continue
        if not (up_slope < low_slope):
            continue

        if up_slope > 0 and low_slope > 0:
            pattern = "상승 쐐기"
            implication = "아래 이탈 주의"
            boundary = lower1
            crossed = float(w["close"].iloc[-1]) < lower1 * 0.998 and float(w["close"].iloc[-2]) >= (low_slope * (x1 - 1) + low_intercept) * 0.998
        elif up_slope < 0 and low_slope < 0:
            pattern = "하락 쐐기"
            implication = "위 이탈 주의"
            boundary = upper1
            crossed = float(w["close"].iloc[-1]) > upper1 * 1.002 and float(w["close"].iloc[-2]) <= (up_slope * (x1 - 1) + up_intercept) * 1.002
        else:
            continue

        close = float(w["close"].iloc[-1])
        previous_close = float(w["close"].iloc[-2])
        top_gap = (upper1 / close - 1) * 100
        bottom_gap = (close / lower1 - 1) * 100
        if min(abs(top_gap), abs(bottom_gap)) > 8 and not crossed:
            continue

        first_vol = float(w["volume"].iloc[:max(10, size // 3)].mean())
        last_vol = float(w["volume"].iloc[-max(10, size // 3):].mean())
        vol20 = float(w["volume"].iloc[-21:-1].mean()) if len(w) >= 21 else 0.0
        vol_ratio = float(w["volume"].iloc[-1] / vol20) if vol20 > 0 else 0.0
        dryup_ratio = last_vol / first_vol if first_vol > 0 else 1.0
        dryup = dryup_ratio <= VOLUME_DRYUP
        if not dryup and not crossed:
            continue

        if crossed and vol_ratio >= BREAK_VOLUME:
            phase = "이탈 확인"
        elif crossed:
            phase = "선 이탈 · 거래량 미확인"
        else:
            phase = "형성 중"

        high_quality = min(len(hi_pts), len(lo_pts))
        score = min(100, int(35 + contraction * 30 + (15 if dryup else 0) + (10 if crossed else 0) + high_quality * 3))
        candidates.append({
            "pattern": pattern,
            "implication": implication,
            "phase": phase,
            "score": score,
            "bars": int(size),
            "close": round(close, 4),
            "upper_line": round(upper1, 4),
            "lower_line": round(lower1, 4),
            "distance_to_line_pct": round((close / boundary - 1) * 100, 2),
            "range_contraction_pct": round(contraction * 100, 1),
            "volume_dryup": bool(dryup),
            "volume_dryup_ratio": round(dryup_ratio, 2),
            "volume_vs_20d": round(vol_ratio, 2),
            "last_close": close,
            "previous_close": previous_close,
        })

    if not candidates:
        return None
    # Prefer a confirmed line break, then volume contraction, then the clearest score.
    candidates.sort(key=lambda x: (x["phase"] == "이탈 확인", x["volume_dryup"], x["score"], x["bars"]), reverse=True)
    result = candidates[0]
    result.pop("last_close", None)
    result.pop("previous_close", None)
    return result
