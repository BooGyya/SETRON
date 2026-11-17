"""AI 원인·현황 설명에 보내는 자료와 결과 형식 (명세 BE-019).

AI에는 화면과 같은 계산 결과만 자료로 보낸다. 숫자는 집계(BE-012)·위험(BE-015)에서 계산하고
AI는 해석과 설명만 맡는다. 답변에 자료에 없는 숫자가 나오면 :class:`AnalysisReport`에 표시한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from setron.features.aggregation.schemas import EquipmentRecord, Summary
from setron.features.aggregation.service import Targets
from setron.features.ai.schemas import AIResult, AnalysisAnswer
from setron.features.item.schemas import Item
from setron.features.item.service import ItemCatalog
from setron.features.risk.service import RiskAssessor
from setron.features.version.service import FamilyReleases


@dataclass(frozen=True, kw_only=True)
class AnalysisInput:
    """분석 자료를 만들 입력. 한 기준일·조회 범위의 설비 현황이다.

    - ``records``: 조회 조건으로 이미 고른 설비 (BE-018 ``select_records`` 결과 등).
    - ``scope``: 화면에 적용된 조회 범위. (조건 이름, 값) 목록이다.
    - ``assessor``: 없으면 위험 자료를 '미계산'으로 둔다.
    - ``item_catalog``·``items``: 없으면 통일 지연 근거(릴리즈 Item)를 보내지 않는다.
    - ``trend``: 날짜별 집계(``summarize_by_date``). 있으면 날짜별 추이를 보낸다.
    - ``history``: 같은 조회 범위의 여러 날짜 관측(기준일 이하). 있으면 버전 변경 이력을 보낸다.
    """

    records: tuple[EquipmentRecord, ...]
    families: Mapping[int, FamilyReleases]
    targets: Targets
    family_names: Mapping[int, str] = field(default_factory=dict)
    scope: tuple[tuple[str, str], ...] = ()
    assessor: RiskAssessor | None = None
    item_catalog: ItemCatalog | None = None
    items: tuple[Item, ...] = ()
    trend: tuple[Summary, ...] = ()
    history: tuple[EquipmentRecord, ...] = ()


@dataclass(frozen=True)
class AnalysisReport:
    """설명 요청 한 번의 결과.

    - ``result``: AI 응답. 실패해도 예외 없이 상태와 안내 문구를 담는다.
    - ``facts``: AI에 보낸 자료. 화면·보고서에서 근거로 함께 보여준다.
    - ``unmatched_numbers``: 답변에 나왔지만 보낸 자료에 없는 숫자. 있으면 화면에서 경고한다.
    """

    result: AIResult[AnalysisAnswer]
    facts: Mapping[str, Any]
    unmatched_numbers: tuple[str, ...] = ()

    @property
    def numbers_match(self) -> bool:
        return not self.unmatched_numbers
