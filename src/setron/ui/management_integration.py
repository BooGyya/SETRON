"""적용된 화면 조건을 관리 현황 출력의 명시적 범위로 전달한다."""

from setron.features.management import ReportFilter
from setron.features.export.schemas import ExportResult, ExportStatus
from pathlib import Path


def export_failure(path, message):
    """공통 앱 구성 코드에 DB·출력 구현을 노출하지 않는 실패 결과."""
    return ExportResult(ExportStatus.FAILED, Path(path), message)


def report_filter(snapshot, source):
    """현재 선택한 실제 설비 집합과 PM 모델 범위를 보존한다. 빈 집합은 전체가 아니다."""
    group = snapshot.query.selection.group_key
    models = tuple(source.bindings[group]) if group is not None else None
    site = snapshot.query.selection.site_key
    sites = tuple(option.label for option in source.catalog.sites if option.key == site) if site is not None else ()
    return ReportFilter(
        site_names=sites,
        equipment_ids=tuple(record.equipment_id for record in snapshot.selected_records),
        model_ids=models,
        family_ids=(snapshot.query.selection.item_family_id,) if snapshot.query.selection.item_family_id is not None else None,
    )
