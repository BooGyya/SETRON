"""대시보드와 버전 리플레이가 공유하는 관측 시점 간격."""

from datetime import date, timedelta


# 재생 간격: None = 저장일마다(실제 저장 날짜), 나머지는 (일, 개월) 간격.
INTERVALS = {None: "저장일마다", "week": "1주", "month": "1개월", "quarter": "3개월"}
_STEPS = {"week": (7, 0), "month": (0, 1), "quarter": (0, 3)}


def _add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def interval_days(start, end, step):
    """시작 날짜부터 간격마다의 날짜. 마지막 간격이 종료 날짜에 딱 맞지 않으면 종료 날짜를 끝에 더한다."""
    days_step, months_step = _STEPS[step]
    result, k = [], 0
    while True:
        day = start + timedelta(days=days_step * k) if days_step else _add_months(start, months_step * k)
        if day > end:
            break
        result.append(day)
        k += 1
    if result and result[-1] != end:
        result.append(end)
    return tuple(result)
