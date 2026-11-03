"""노후도 기간 기준을 `app_setting`에서 읽고 저장한다 (명세 BE-016, FUN-073).

저장은 공통 저장 서비스(`features/storage`)의 트랜잭션을 쓴다. 잘못된 기준은 저장하지 않는다.
"""

from __future__ import annotations

from setron.features.aging.schemas import (
    DEFAULT_THRESHOLDS,
    SETTING_KEY,
    AgeThresholds,
    LoadedThresholds,
)
from setron.features.aging.service import parse_thresholds, threshold_problems

_KEY = {"setting_key": SETTING_KEY}


def thresholds_in(tx) -> LoadedThresholds:
    """열린 트랜잭션에서 기간 기준을 읽는다. 행이 없으면 기본값, 값이 잘못되었으면 문제 목록."""
    try:
        value = tx.get("app_setting", _KEY)["setting_value"]
    except LookupError:
        return LoadedThresholds(DEFAULT_THRESHOLDS, is_default=True)
    problems = threshold_problems(value)
    if problems:
        return LoadedThresholds(None, problems)
    return LoadedThresholds(parse_thresholds(value))


class AgingSettingsRepository:
    """``storage``는 ``transaction()``으로 ``get``·``insert``·``update``를 제공하는 저장 서비스다."""

    def __init__(self, storage) -> None:
        self.storage = storage

    def load(self) -> LoadedThresholds:
        """저장된 기준을 읽는다. 행이 없으면 기본값(1년·2년), 값이 잘못되었으면 문제 목록을 돌려준다."""
        with self.storage.transaction() as tx:
            return thresholds_in(tx)

    def save(self, value: object) -> AgeThresholds:
        """기준을 검사한 뒤 저장한다. 음수·0·역전·단위 오류는 ``ThresholdError``로 알리고 저장하지 않는다."""
        thresholds = parse_thresholds(value)
        with self.storage.transaction() as tx:
            try:
                tx.get("app_setting", _KEY)
            except LookupError:
                tx.insert("app_setting", {**_KEY, "setting_value": thresholds.to_setting()})
            else:
                tx.update("app_setting", _KEY, {"setting_value": thresholds.to_setting()})
        return thresholds
