"""저장 전 검토에 필요한 추출값·원본 위치·확인 사유."""

from dataclasses import dataclass
from typing import Any

from setron.features.ai.schemas import ExtractKind


@dataclass(frozen=True)
class CellSource:
    sheet: str
    address: str
    value: Any


@dataclass(frozen=True)
class ExtractionIssue:
    reason: str
    field: str | None = None
    address: str | None = None
    # Excel 행 번호가 아니라 추출 결과에서 1부터 시작하는 행 번호다.
    row_number: int | None = None
    code: str | None = None
    blocks_save: bool = False


@dataclass(frozen=True)
class FamilyKey:
    """검토용 계열 구분. DB ID나 확인된 모델 대응을 뜻하지 않는다.

    C3 형상과 생산 영역은 키에 넣지 않는다. KCTC와 Oxide 및 C3 UI는 구분한다.
    """

    category: str
    family_name: str
    ui_type: str | None = None
    structure_type: str | None = None
    model_name: str | None = None


@dataclass(frozen=True)
class ReviewRow:
    values: dict[str, str | None]
    raw_values: dict[str, str | None]
    sources: dict[str, CellSource]
    family_key: FamilyKey | None
    issues: tuple[ExtractionIssue, ...] = ()

    @property
    def source_reference(self) -> dict:
        """기존 설비·릴리즈 저장 서비스가 받는 필드별 원본 위치 형식."""
        return {field: {"sheet": source.sheet, "cells": [source.address]}
                for field, source in self.sources.items()}


@dataclass(frozen=True)
class ExtractionReview:
    """한 파일·한 시트의 검토 자료. DB 저장이나 저장 가능 판정은 수행하지 않는다.

    workbook_name은 확인창 표시 전용이다. raw_data에는 파일명을 넣지 않는다.
    BE-008 검증·BE-009 저장 단계는 전체 묶음의 검토 결과를 별도로 확인해야 한다.
    """

    kind: ExtractKind
    workbook_name: str
    sheet_name: str
    rows: tuple[ReviewRow, ...]
    issues: tuple[ExtractionIssue, ...]
    raw_data: dict

    @property
    def needs_review(self) -> bool:
        return bool(self.issues)
