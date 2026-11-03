# -*- coding: utf-8 -*-
"""버전 통일 예측 — 단순 비례 (멘토 10/21: 보조 기능, 정확도 민감하지 않음, 데이터 적으면 비례 계산)

입력: 날짜별 스냅샷의 (기준일, 적용 대수, 대상 대수) 목록 — 같은 선택 범위(설비군·사이트·라인)와 같은 N 으로 센 것
계산: 속도 = (마지막 적용 대수 − 처음 적용 대수) ÷ 경과 일수, 남은 대수 ÷ 속도 = 남은 일수
표시: '참고용(데이터 n장)'. 스냅샷이 1장이거나 증가가 없으면 예측 불가.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass
class SnapshotPoint:
    base_date: date
    applied: int
    total: int


@dataclass
class Forecast:
    status: str                    # OK / DONE / NOT_ENOUGH_DATA / NO_PROGRESS
    points: int
    rate_per_day: Optional[float] = None
    remaining: Optional[int] = None
    est_days: Optional[int] = None
    est_date: Optional[date] = None
    note: str = ""

    @property
    def message(self) -> str:
        if self.status == "DONE":
            return f"이미 전부 적용됨 (참고용, 데이터 {self.points}장)"
        if self.status == "NOT_ENOUGH_DATA":
            return f"스냅샷이 {self.points}장이라 예측 불가 — 날짜가 다른 현황표가 2장 이상 필요"
        if self.status == "NO_PROGRESS":
            return f"기간 동안 적용 대수가 늘지 않아 예측 불가 (참고용, 데이터 {self.points}장)"
        return (f"남은 {self.remaining}대, 최근 속도 {self.rate_per_day:.2f}대/일 → 약 {self.est_days}일 뒤 "
                f"{self.est_date.isoformat()} 완료 예상 (참고용, 데이터 {self.points}장)")


def forecast(points: list[SnapshotPoint]) -> Forecast:
    pts = sorted(points, key=lambda p: p.base_date)
    n = len(pts)
    if not pts:
        return Forecast("NOT_ENOUGH_DATA", 0)
    last = pts[-1]
    remaining = last.total - last.applied
    if remaining <= 0:
        return Forecast("DONE", n, remaining=0)
    if n < 2:
        return Forecast("NOT_ENOUGH_DATA", n, remaining=remaining)
    first = pts[0]
    days = (last.base_date - first.base_date).days
    gained = last.applied - first.applied
    if days <= 0 or gained <= 0:
        return Forecast("NO_PROGRESS", n, remaining=remaining)
    rate = gained / days
    est_days = int(round(remaining / rate))
    return Forecast("OK", n, rate, remaining, est_days, last.base_date + timedelta(days=est_days),
                    f"{first.base_date}→{last.base_date} 사이 {gained}대 증가")
