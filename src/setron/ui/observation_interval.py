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
    """시작일부터 지정 일수 또는 기존 주·월 간격의 날짜를 만들고 종료일도 포함한다."""
    if isinstance(step, int):
        if isinstance(step, bool) or step <= 0:
            raise ValueError("관측 간격은 1일 이상의 정수여야 합니다.")
        # 종료일까지의 차이 안에서만 더하므로 아주 큰 지정 간격도 날짜 범위를 넘지 않는다.
        result = [start + timedelta(days=offset) for offset in range(0, (end - start).days + 1, step)]
    else:
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
