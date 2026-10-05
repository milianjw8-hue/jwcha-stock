#!/usr/bin/env python3
"""Fetch delayed Yahoo index closes: 5 years of daily pairs and latest 10 weeks."""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
OUTPUTS = (ROOT / "weekly-data.json", ROOT / "docs" / "weekly-data.json")
TICKERS = {"nasdaq": "%5EIXIC", "nyse": "%5ENYA"}
HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")


def get_chart(ticker: str) -> dict:
    errors: list[str] = []
    for host in HOSTS:
        url = f"https://{host}/v8/finance/chart/{ticker}?range=5y&interval=1d"
        for attempt in range(3):
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; WeeklyIndexCalculator/1.0)",
                    "Accept": "application/json",
                    "Referer": "https://finance.yahoo.com/",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    payload = json.load(response)
                result = payload["chart"]["result"][0]
                timestamps = result["timestamp"]
                closes = result["indicators"]["quote"][0]["close"]
                series: dict[dt.date, float] = {}
                for stamp, close in zip(timestamps, closes):
                    if close is None:
                        continue
                    day = dt.datetime.fromtimestamp(stamp, ZoneInfo("America/New_York")).date()
                    series[day] = float(close)
                if len(series) < 10:
                    raise ValueError(f"only {len(series)} closes returned for {ticker}")
                return series
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                errors.append(f"{host} attempt {attempt + 1}: {exc}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
    raise RuntimeError("Could not fetch " + ticker + ": " + "; ".join(errors))


def iso_week(day: dt.date) -> tuple[int, int]:
    iso = day.isocalendar()
    return iso.year, iso.week


def weekly_map(series: dict[dt.date, float]) -> dict[tuple[int, int], tuple[dt.date, float]]:
    result: dict[tuple[int, int], tuple[dt.date, float]] = {}
    for day, close in sorted(series.items()):
        result[iso_week(day)] = (day, close)
    return result


def main() -> None:
    nasdaq_daily = get_chart(TICKERS["nasdaq"])
    nyse_daily = get_chart(TICKERS["nyse"])
    shared_days = sorted(set(nasdaq_daily) & set(nyse_daily))
    if len(shared_days) < 500:
        raise RuntimeError(f"Expected at least 500 aligned daily closes, received {len(shared_days)}")

    daily = [{
        "date": day.isoformat(),
        "nasdaq": round(nasdaq_daily[day], 2),
        "nyse": round(nyse_daily[day], 2),
    } for day in shared_days]

    nasdaq = weekly_map(nasdaq_daily)
    nyse = weekly_map(nyse_daily)
    shared_weeks = sorted(set(nasdaq) & set(nyse))[-10:]
    if len(shared_weeks) != 10:
        raise RuntimeError(f"Expected 10 aligned weeks, received {len(shared_weeks)}")

    weeks = []
    for week in reversed(shared_weeks):
        nd, nv = nasdaq[week]
        yd, yv = nyse[week]
        weeks.append({
            "date": min(nd, yd).isoformat(),
            "nasdaq": round(nv, 2),
            "nyse": round(yv, 2),
        })

    payload = {
        "fetchedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": "Yahoo Finance delayed index data · 5-year daily history",
        "weeks": weeks,
        "daily": daily,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    for output in OUTPUTS:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    print(f"Wrote {len(daily)} paired daily closes and {len(weeks)} weekly closes; latest: {weeks[0]['date']}")


if __name__ == "__main__":
    main()
