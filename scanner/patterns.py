#!/usr/bin/env python3
"""차트 패턴 탐지 — 영상 47:46 이후 구간의 패턴을 숫자 기준으로 구현한다.

원칙: 패턴은 **매수 신호가 아니라 이미 스캐너를 통과한 후보의 가점**이다.
천정 패턴(헤드앤숄더·쌍봉·이브닝스타)만 보유 종목 청산 경보로 직접 쓴다.
자동 패턴 인식은 오탐이 많으므로 판정 기준을 느슨하게 두지 않는다.
"""
from __future__ import annotations

import pandas as pd

from indicators import swing_points

# ── 판정 상수 ─────────────────────────────────────────────────────────
CUP_MIN_BARS = 35          # 7주
CUP_DEPTH = (0.12, 0.33)   # 오닐 기준 12~33%
HANDLE_MAX_DEPTH = 0.15
HANDLE_VOL_RATIO = 0.70    # 손잡이 거래량 < 베이스의 70%
DB_MIN_GAP = 15            # 두 바닥 최소 간격(거래일)
DB_TOL = 0.03              # 두 바닥 가격차 허용
HS_SHOULDER_TOL = 0.06     # 양 어깨 높이차 허용
VCP_SHRINK = 0.90          # 다음 수축은 직전의 90% 이하여야 축소로 인정


def _body_ratio(row) -> float:
    rng = row["high"] - row["low"]
    return abs(row["close"] - row["open"]) / rng if rng > 0 else 0.0


def _candles(df: pd.DataFrame) -> dict:
    """캔들 조합 — 적삼병/흑삼병, 도지, 모닝·이브닝스타."""
    out: dict = {}
    if len(df) < 4:
        return out
    d = df.tail(4).reset_index(drop=True)
    c1, c2, c3 = d.iloc[1], d.iloc[2], d.iloc[3]

    up = [r["close"] > r["open"] for r in (c1, c2, c3)]
    dn = [r["close"] < r["open"] for r in (c1, c2, c3)]
    bodies = [abs(r["close"] - r["open"]) for r in (c1, c2, c3)]
    vols = [r["volume"] for r in (c1, c2, c3)]

    # 적삼병 — 3연속 양봉 + 종가 순차 상승 + 몸통 유지 + 거래량 비감소
    out["three_white_soldiers"] = bool(
        all(up)
        and c3["close"] > c2["close"] > c1["close"]
        and bodies[1] >= bodies[0] * 0.5 and bodies[2] >= bodies[1] * 0.5
        and vols[2] >= vols[0] * 0.8
    )
    out["three_black_crows"] = bool(
        all(dn)
        and c3["close"] < c2["close"] < c1["close"]
        and bodies[1] >= bodies[0] * 0.5 and bodies[2] >= bodies[1] * 0.5
    )

    out["doji"] = bool(_body_ratio(c3) <= 0.10)

    # 모닝스타 / 이브닝스타 — 3번 캔들이 1번 몸통의 절반 이상 회복해야 유효
    b1_mid = (c1["open"] + c1["close"]) / 2
    small_middle = _body_ratio(c2) <= 0.35
    out["morning_star"] = bool(
        dn[0] and _body_ratio(c1) >= 0.4 and small_middle
        and up[2] and c3["close"] >= b1_mid
    )
    out["evening_star"] = bool(
        up[0] and _body_ratio(c1) >= 0.4 and small_middle
        and dn[2] and c3["close"] <= b1_mid
    )
    return out


def _vcp(df: pd.DataFrame, lookback: int = 120) -> dict:
    """VCP 수축 — 조정폭이 순차적으로 줄고 마지막 구간 거래량이 마르는가."""
    out: dict = {"vcp_contractions": 0, "vcp_shrinking": False,
                 "vcp_vol_dryup": False, "vcp_depths": []}
    d = df.tail(lookback).reset_index(drop=True)
    if len(d) < 40:
        return out

    highs, lows = swing_points(d, k=4)
    if not highs or not lows:
        return out

    # 고점 → 그 뒤 첫 저점 쌍으로 조정폭 계산
    depths, spans = [], []
    for hi in highs:
        after = [lo for lo in lows if lo > hi]
        if not after:
            continue
        lo = after[0]
        hp, lp = float(d["high"].iloc[hi]), float(d["low"].iloc[lo])
        if hp <= 0:
            continue
        depth = (hp - lp) / hp
        if depth >= 0.03:                    # 3% 미만은 노이즈
            depths.append(round(depth * 100, 1))
            spans.append((hi, lo))

    depths, spans = depths[-4:], spans[-4:]
    out["vcp_depths"] = depths
    out["vcp_contractions"] = len(depths)
    if len(depths) >= 2:
        out["vcp_shrinking"] = all(
            depths[i + 1] <= depths[i] * VCP_SHRINK for i in range(len(depths) - 1)
        )
        # 마지막 수축 구간의 평균 거래량 vs 베이스 전체 평균
        start = spans[0][0]
        last_start = spans[-1][0]
        base_vol = float(d["volume"].iloc[start:].mean())
        last_vol = float(d["volume"].iloc[last_start:].mean())
        out["vcp_vol_dryup"] = bool(base_vol > 0 and last_vol < base_vol * HANDLE_VOL_RATIO)
    return out


def _cup_handle(df: pd.DataFrame, lookback: int = 160) -> dict:
    """컵앤핸들 — 오닐 기준(깊이 12~33%, 7주 이상, 얕은 손잡이, 손잡이 거래량 급감)."""
    out: dict = {"cup_handle": False}
    d = df.tail(lookback).reset_index(drop=True)
    if len(d) < CUP_MIN_BARS + 10:
        return out

    hi_s, lo_s = d["high"], d["low"]
    # 왼쪽 림 = 앞 절반의 최고가
    half = len(d) // 2
    left_i = int(hi_s.iloc[:half].idxmax())
    left = float(hi_s.iloc[left_i])
    if left <= 0:
        return out

    # 컵 바닥 = 왼쪽 림 이후 최저가
    rest_low = lo_s.iloc[left_i + 1:]
    if rest_low.empty:
        return out
    bottom_i = int(rest_low.idxmin())
    bottom = float(lo_s.iloc[bottom_i])
    depth = (left - bottom) / left
    if not (CUP_DEPTH[0] <= depth <= CUP_DEPTH[1]):
        return out

    # 오른쪽 림 = 바닥 이후 최고가. 왼쪽 림의 92% 이상까지 회복해야 컵이다.
    rest_high = hi_s.iloc[bottom_i + 1:]
    if rest_high.empty:
        return out
    right_i = int(rest_high.idxmax())
    right = float(hi_s.iloc[right_i])
    if right < left * 0.92:
        return out
    if (right_i - left_i) < CUP_MIN_BARS:
        return out

    # 손잡이 = 오른쪽 림 이후 눌림. 얕고(≤15%) 컵 상단 절반 안에 있어야 한다.
    # "상단 1/3"으로 잡으면 깊은 컵(20%↑)에서 15% 손잡이와 서로 모순되어 정상 셋업을
    # 걸러낸다. 오닐의 원 기준인 "컵 중간값 위"를 쓴다.
    handle = d.iloc[right_i + 1:]
    if len(handle) < 3:
        return out
    h_low = float(handle["low"].min())
    h_depth = (right - h_low) / right
    cup_mid = right - (right - bottom) / 2
    if h_depth > HANDLE_MAX_DEPTH or h_low < cup_mid:
        return out

    base_vol = float(d["volume"].iloc[left_i:right_i + 1].mean())
    h_vol = float(handle["volume"].mean())
    if not (base_vol > 0 and h_vol < base_vol * HANDLE_VOL_RATIO):
        return out

    out.update({
        "cup_handle": True,
        "cup_depth_pct": round(depth * 100, 1),
        "cup_bars": int(right_i - left_i),
        "cup_handle_pivot": round(float(handle["high"].max()), 4),
        "handle_depth_pct": round(h_depth * 100, 1),
    })
    return out


def _double(df: pd.DataFrame, lookback: int = 150) -> dict:
    """쌍바닥(W) / 쌍봉(M) — 넥라인과 확정 여부까지."""
    out: dict = {"double_bottom": False, "double_top": False}
    d = df.tail(lookback).reset_index(drop=True)
    if len(d) < 40:
        return out
    highs, lows = swing_points(d, k=4)
    close = float(d["close"].iloc[-1])

    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        p1, p2 = float(d["low"].iloc[i1]), float(d["low"].iloc[i2])
        if (i2 - i1) >= DB_MIN_GAP and p1 > 0 and abs(p2 - p1) / p1 <= DB_TOL:
            neck = float(d["high"].iloc[i1:i2 + 1].max())
            v1 = float(d["volume"].iloc[max(0, i1 - 2):i1 + 3].mean())
            v2 = float(d["volume"].iloc[max(0, i2 - 2):i2 + 3].mean())
            # 두 번째 바닥의 거래량이 더 적어야 매도 물량 고갈로 본다
            if v1 > 0 and v2 < v1:
                out.update({
                    "double_bottom": True,
                    "db_neckline": round(neck, 4),
                    "db_confirmed": bool(close > neck),
                    "db_target": round(neck + (neck - min(p1, p2)), 4),
                })

    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        p1, p2 = float(d["high"].iloc[i1]), float(d["high"].iloc[i2])
        if (i2 - i1) >= DB_MIN_GAP and p1 > 0 and abs(p2 - p1) / p1 <= DB_TOL:
            neck = float(d["low"].iloc[i1:i2 + 1].min())
            out.update({
                "double_top": True,
                "dt_neckline": round(neck, 4),
                "dt_confirmed": bool(close < neck),
            })
    return out


def _head_shoulders(df: pd.DataFrame, lookback: int = 160) -> dict:
    """헤드앤숄더 — 고점 갱신 실패 + 넥라인 이탈. 청산 경보 전용."""
    out: dict = {"head_shoulders": False, "hs_neckline": None, "hs_broken": False}
    d = df.tail(lookback).reset_index(drop=True)
    if len(d) < 50:
        return out
    highs, lows = swing_points(d, k=4)
    if len(highs) < 3 or len(lows) < 2:
        return out

    ls, head, rs = highs[-3], highs[-2], highs[-1]
    hl, hh, hr = (float(d["high"].iloc[i]) for i in (ls, head, rs))
    if not (hh > hl and hh > hr):
        return out
    if hl <= 0 or abs(hr - hl) / hl > HS_SHOULDER_TOL:
        return out

    mids = [lo for lo in lows if ls < lo < rs]
    if len(mids) < 2:
        return out
    neck = (float(d["low"].iloc[mids[0]]) + float(d["low"].iloc[mids[-1]])) / 2
    close = float(d["close"].iloc[-1])
    out.update({
        "head_shoulders": True,
        "hs_neckline": round(neck, 4),
        "hs_broken": bool(close < neck),
        "hs_target": round(neck - (hh - neck), 4),
    })
    return out


def detect_patterns(df: pd.DataFrame) -> dict:
    """모든 패턴 지표를 하나의 딕셔너리로."""
    out: dict = {}
    for fn in (_candles, _vcp, _cup_handle, _double, _head_shoulders):
        try:
            out.update(fn(df))
        except Exception as e:  # 패턴 하나가 실패해도 스캔 전체는 계속되어야 한다
            print(f"패턴 탐지 실패 {fn.__name__}: {e}")

    # 천정 경보 — 보유 종목 청산 판단에만 쓴다
    out["topping_alert"] = bool(
        out.get("hs_broken") or out.get("dt_confirmed") or out.get("evening_star")
    )
    return out
