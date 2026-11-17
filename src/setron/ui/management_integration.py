"""관리 데이터 내보내기의 파일명과 조회 범위를 처리한다."""

import re
from itertools import count
from pathlib import Path

from setron.features.management import ReportFilter
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.ui.query_state import selection_keys


def export_failure(path, message):
    """공통 앱 구성 코드에 DB·출력 구현을 노출하지 않는 실패 결과."""
    return ExportResult(ExportStatus.FAILED, Path(path), message)


def save_numbered_export(exporter, path, suffix, *, confirm_duplicate=None):
    """동명 파일을 보존하고, 저장할 이름은 파일을 배타적으로 생성해 확보한다."""
    destination = reserve_numbered_export(path, suffix, confirm_duplicate=confirm_duplicate)
    if not isinstance(destination, Path):
        return destination
    return write_reserved_export(exporter, destination)


def reserve_numbered_export(path, suffix, *, confirm_duplicate=None):
    """확인은 호출 스레드에서 마치고, 작업자가 쓸 빈 파일을 확보한다."""
    requested = Path(path)
    if requested.suffix.lower() != suffix:
        return export_failure(requested, f"파일 이름은 {suffix}로 끝나야 합니다.")
    # 이전에 중복으로 붙은 번호가 있더라도 첫 번호부터 같은 파일명 계열을 이어 간다.
    numbered = re.fullmatch(r"(.+?) \(([1-9][0-9]*)\)(?: \([1-9][0-9]*\))*", requested.stem)
    stem, first_number = (numbered[1], int(numbered[2]) + 1) if numbered else (requested.stem, 1)
    path = requested
    for number in count(first_number):
        try:
            with path.open("xb"):
                pass
        except FileExistsError:
            if path == requested and confirm_duplicate is not None and not confirm_duplicate(requested):
                return None
            path = requested.with_name(f"{stem} ({number}){requested.suffix}")
            continue
        except OSError:
            return export_failure(path, "파일을 저장할 수 없습니다. 폴더 쓰기 권한을 확인해 주세요.")
        break

    return path


def write_reserved_export(exporter, path):
    """확보한 파일에 쓰고 실패하면 이번 작업의 파일만 정리한다."""
    saved = False
    try:
        result = exporter(path)
        saved = result.saved
        return result
    finally:
        if not saved:
            path.unlink(missing_ok=True)


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
