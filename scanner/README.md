# 스윙 스캐너

GitHub Actions가 평일마다 자동 실행하여 결과를 `docs/`에 커밋하고, PWA(스윙 콘솔) 운영 탭이 이를 표시한다.

| 워크플로 | 스케줄 (KST) | 스크립트 | 출력 |
|---|---|---|---|
| 국장 스캔 | 평일 16:10 | `scan_kr.py` | `docs/results.json` |
| 미장 스캔 | 평일 06:30 | `scan_us.py` | `docs/results_us.json` |

Actions 탭에서 **Run workflow** 버튼으로 수동 실행도 가능.

## 스캔 조건

**국장** (`scan_kr.py`) — pykrx(KRX 공식 데이터) 사용
- 당일 거래대금 상위 150종목 중 (2,000원 미만 동전주 제외)
- 52주 고점 대비 80% 이상 + 거래량 20일 평균의 2배 이상
- 직전 60거래일 고가 돌파 시 🔥 표시
- 시장필터: KOSPI 종가 > 20일선 & 60일선

**미장** (`scan_us.py`) — yfinance 사용, S&P500 + Nasdaq-100 유니버스
- 미너비니 트렌드 템플릿: 종가 > 50일선 > 150일선 > 200일선(상승 중), 52주 고점 대비 75%↑, 52주 저점 대비 1.3배↑
- RS 백분위 70 이상 (가중 수익률 기준)
- 셋업 분류: 🔥돌파(피봇 상향 돌파+거래량 1.5배) / 🎯피봇 근접(3% 이내) / 🧱베이스 / ↗이탈
- 시장필터: SPY 종가 > 50일선 > 200일선

## KRX 계정 등록 (국장 필수)

국장 데이터를 받는 `pykrx` 는 **data.krx.co.kr(KRX 정보데이터시스템) 로그인**을 요구한다.
계정이 없으면 국장 스캔·포지션 감시·백테스트가 모두 실패한다. 미장은 영향 없다.

### 1) 계정 만들기

1. https://data.krx.co.kr 접속
2. 우측 상단 **회원가입** → 개인회원
3. 아이디·비밀번호를 정하고 가입 (무료)
4. 가입한 아이디로 한 번 로그인해 정상 동작을 확인

### 2) GitHub 에 등록

저장소 → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret** 을 두 번 누른다.

| Name | Secret |
|---|---|
| `KRX_ID` | 가입한 아이디 |
| `KRX_PW` | 비밀번호 |

이름은 **대문자로 정확히** 입력한다. 등록 후 값은 다시 볼 수 없고 덮어쓰기만 된다.

### 3) 확인

Actions → **국장 스캔** → **Run workflow** 로 즉시 실행해 본다.
성공하면 `docs/results.json` 이 갱신되고 앱 국장 탭에 후보가 나온다.

### 로컬 PC 에서 돌릴 때

같은 계정을 환경 변수로 넣는다. 코드에 적어두지 않는다.

**Windows (PowerShell)**
```powershell
$env:KRX_ID="아이디"
$env:KRX_PW="비밀번호"
python scanner\scan_kr.py
```

**Windows (명령 프롬프트)**
```cmd
set KRX_ID=아이디
set KRX_PW=비밀번호
python scanner\scan_kr.py
```

**Mac / Linux**
```bash
export KRX_ID='아이디'
export KRX_PW='비밀번호'
python scanner/scan_kr.py
```

창을 닫으면 사라진다. 매번 치기 싫으면 Windows 는 *시스템 환경 변수 편집*,
Mac 은 `~/.zshrc` 에 같은 줄을 추가한다.

> 비밀번호를 저장소에 커밋하지 않는다. `.env` 파일을 만들더라도 `.gitignore` 에
> 넣어야 한다. GitHub Secrets 에 넣은 값은 로그에 자동으로 가려진다.

## 텔레그램 푸시 (선택)

리포 Settings → Secrets and variables → Actions 에 아래 두 시크릿을 추가하면 스캔 결과가 텔레그램으로 푸시된다. 없으면 푸시만 생략되고 스캔은 정상 동작.

1. `TELEGRAM_BOT_TOKEN` — 텔레그램에서 [@BotFather](https://t.me/BotFather)에게 `/newbot` → 발급받은 토큰
2. `TELEGRAM_CHAT_ID` — 만든 봇에게 아무 메시지 전송 후 `https://api.telegram.org/bot<토큰>/getUpdates` 접속 → `chat.id` 값
