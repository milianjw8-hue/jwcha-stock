# 룰셋 조건 DSL

스캐너(Python)·앱(JS)·백테스트가 같은 JSON을 읽는다. `eval`을 쓰지 않는 구조화된
조건식이며, 평가기는 `rules/rules.py`(Python)와 `docs/app.js`의 `evalCond`(JS)에
동일한 규칙으로 구현되어 있다. 한쪽을 고치면 반드시 다른 쪽도 고친다.

## 룰셋 파일 구조

```jsonc
{
  "id": "us_minervini",
  "market": "us",              // "us" | "kr"
  "label": "미국장 — 미너비니 주도주",
  "verdict": { "go": 85, "wait": 70 },   // 가중 점수(%) 임계값
  "groups": [
    {
      "id": "trend_template",
      "title": "Trend Template — 시장 지위 필터",
      "note": "8개 중 7개 이상 충족해야 후보.",
      "items": [
        {
          "id": "us_a1",
          "t":  "현재가가 150일선·200일선 위",   // 표시 문구
          "h":  "둘 중 하나라도 아래면 후보 제외", // 보조 설명 (선택)
          "w":  8,                               // 가중치
          "core": true,                          // 하나라도 미충족이면 진입 불가
          "auto": { ... }                        // 없으면 수동 판단 항목
        }
      ]
    }
  ]
}
```

## 조건식

### 비교
```jsonc
{ "m": "pct_of_52wk_high", "op": ">=", "v": 75 }   // 지표 vs 상수
{ "m": "close",            "op": ">",  "m2": "ma150" }  // 지표 vs 지표
```

연산자: `>`, `>=`, `<`, `<=`, `==`, `!=`

### 결합
```jsonc
{ "all": [cond, cond, ...] }   // AND
{ "any": [cond, cond, ...] }   // OR
{ "not": cond }
```

## 평가 규칙

- 참조한 지표가 `metrics`에 없거나 `null`이면 조건 결과는 **`null`(판정 불가)** 이다.
  `false`가 아니다. 판정 불가 항목은 자동 체크되지 않고 사람이 판단하도록 남는다.
- `all`은 하나라도 `false`면 `false`, `false`가 없고 `null`이 있으면 `null`.
- `any`는 하나라도 `true`면 `true`, `true`가 없고 `null`이 있으면 `null`.
- 불리언 지표는 `{"m":"breakout_60","op":"==","v":true}` 형태로 비교한다.

## 점수 계산

```
pct      = (체크된 항목 w 합계) / (전체 w 합계) × 100
coreMiss = core:true 인데 미체크인 항목 수

coreMiss > 0        → "핵심 미충족"  (진입 불가)
pct >= verdict.go   → "진입 가능"
pct >= verdict.wait → "관망"
그 외               → "제외"
```

## 지표 어휘

`scanner/indicators.py`가 산출해 `results*.json`의 `candidates[].metrics`에 실린다.
전체 목록은 `docs/PLAN.md` §2 표를 참조한다.
