"""텔레그램 푸시 헬퍼 — 봇 토큰/챗 ID 미설정 시 조용히 생략."""
import os

import requests


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("텔레그램 미설정 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) — 푸시 생략")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        r.raise_for_status()
        print("텔레그램 푸시 완료")
        return True
    except Exception as e:  # 푸시 실패가 스캔 자체를 실패시키면 안 됨
        print(f"텔레그램 푸시 실패: {e}")
        return False
