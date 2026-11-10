"""적용된 화면 조건을 관리 현황 출력의 명시적 범위로 전달한다."""

from setron.features.management import ReportFilter
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.ui.query_state import selection_keys
from pathlib import Path


def export_failure(path, message):
    """공통 앱 구성 코드에 DB·출력 구현을 노출하지 않는 실패 결과."""
    return ExportResult(ExportStatus.FAILED, Path(path), message)


def report_filter(snapshot, source):
    """현재 선택한 실제 설비 집합과 PM 모델 범위를 보존한다. 빈 집합은 전체가 아니다."""
    selection = snapshot.query.selection
    groups = selection_keys(selection.group_key)
    models = tuple(model for group in groups for model in source.bindings[group]) if groups else None
    site_keys = selection_keys(selection.site_key)
    sites = tuple(option.label for option in source.catalog.sites if option.key in site_keys)
    return ReportFilter(
        site_names=sites,
        equipment_ids=tuple(record.equipment_id for record in snapshot.selected_records),
        model_ids=models,
        family_ids=(selection.item_family_id,) if selection.item_family_id is not None else None,
    )
