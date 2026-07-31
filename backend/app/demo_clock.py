# 데모 고정 시각. 실제 서버 시각과 무관하게 데모 시나리오가 항상 동일하게
# 재현되도록 "지금"을 고정한다 (prototype.html 상단바 "월 10일 9:41"과 동일).
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DEMO_NOW = datetime(2026, 8, 10, 9, 41, 0, tzinfo=KST)


def now() -> datetime:
    return DEMO_NOW


def now_iso() -> str:
    return DEMO_NOW.isoformat(timespec="seconds")


def today() -> date:
    return DEMO_NOW.date()
