"""한 업로드 양식의 시트별 배치·자료 조건을 검증한 뒤 한 파일로 저장한다."""

from dataclasses import dataclass
import os
from pathlib import Path
from zipfile import BadZipFile

from lxml import etree

from setron.features.export import template as renderer
from setron.features.export.management import read_management_records_by_scope
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.infrastructure.database import DatabaseError


@dataclass(frozen=True)
class SheetExportConfig:
    template: renderer.TemplateConfig
    line: str | None = None
    production_area: str | None = None
    site: str | None = None


@dataclass(frozen=True)
class WorkbookTemplateConfig:
    path: Path
    sheets: tuple[SheetExportConfig, ...]
    empty_policy: str = "clear"


MAX_CONFIGURED_SHEETS = 100
MAX_WORKBOOK_CHANGED_CELLS = 500_000


def validate_workbook_config(config):
    """시트·원본 파일의 중복과 빈 자료 처리 방식을 확인한다."""
    if not isinstance(config, WorkbookTemplateConfig):
        raise ValueError("시트별 양식 설정을 확인해 주세요.")
    if not isinstance(config.empty_policy, str) or config.empty_policy not in {"clear", "keep", "error"}:
        raise ValueError("자료가 없는 시트의 처리 방식을 선택해 주세요.")
    if (not isinstance(config.sheets, tuple) or not config.sheets
            or len(config.sheets) > MAX_CONFIGURED_SHEETS):
        raise ValueError("내보낼 시트를 1개 이상, 100개 이하로 설정해 주세요.")
    path = Path(config.path)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("맞춤 양식은 .xlsx 파일을 선택해 주세요.")
    names, fingerprint = set(), None
    for settings in config.sheets:
        if not isinstance(settings, SheetExportConfig) or not isinstance(settings.template, renderer.TemplateConfig):
            raise ValueError("시트별 입력 영역과 항목 연결을 확인해 주세요.")
        layout = settings.template
        if Path(layout.path).resolve() != path.resolve():
            raise ValueError("한 번에 저장할 시트는 같은 원본 양식 파일을 사용해야 합니다.")
        if (not isinstance(layout.sheet_name, str) or not layout.sheet_name
                or layout.sheet_name in names):
            raise ValueError("같은 시트에는 하나의 배치와 자료 조건만 설정할 수 있습니다.")
        names.add(layout.sheet_name)
        if not isinstance(layout.fingerprint, str) or not layout.fingerprint:
            raise ValueError("양식을 먼저 불러와 시트별 매핑과 미리보기를 확인해 주세요.")
        if fingerprint is None:
            fingerprint = layout.fingerprint
        elif layout.fingerprint != fingerprint:
            raise ValueError("시트별 양식의 검토 시점이 다릅니다. 같은 파일을 다시 불러와 확인해 주세요.")
        if any(value is not None and not isinstance(value, str)
               for value in (settings.line, settings.production_area, settings.site)):
            raise ValueError("시트의 사업장·라인·생산 영역 조건을 확인해 주세요.")
        if any(value is not None and (not isinstance(value, str) or not value.strip())
               for value in (settings.line, settings.site, layout.site)):
            raise ValueError("사업장과 라인을 입력하거나 ‘전체’를 명시적으로 선택해 주세요.")
        if settings.site is not None and layout.site is not None and settings.site != layout.site:
            raise ValueError("시트 조건과 양식에 선택한 사업장이 서로 다릅니다.")
    return config


def _scope(settings):
    return {"site": settings.template.site if settings.site is None else settings.site,
            "line": settings.line, "production_area": settings.production_area}


def read_workbook_records(storage, config, *, production_area=None, batch_ids=None):
    """모든 대상 시트의 자료를 같은 DB 읽기 스냅샷으로 준비한다."""
    validate_workbook_config(config)
    scopes = {settings.template.sheet_name: _scope(settings) for settings in config.sheets}
    return read_management_records_by_scope(storage, scopes, production_area=production_area, batch_ids=batch_ids)


def _load_source(config):
    validate_workbook_config(config)
    source = renderer.load_template(config.path, config.sheets[0].template.fingerprint)
    paths = []
    for settings in config.sheets:
        name = settings.template.sheet_name
        if name not in source.sheets:
            raise ValueError(f"{name}: 원본 양식에서 시트를 찾을 수 없습니다. 시트 설정을 다시 확인해 주세요.")
        paths.append(source.sheets[name])
    if len(set(paths)) != len(paths):
        raise ValueError("여러 시트가 같은 내부 영역을 참조하는 양식은 저장할 수 없습니다.")
    return source


def _prepare_workbook(records_by_sheet, config):
    source = _load_source(config)
    if not isinstance(records_by_sheet, dict):
        raise ValueError("시트별로 내보낼 현재 자료를 확인할 수 없습니다.")
    prepared, summaries = [], []
    changed_cells = 0
    for settings in config.sheets:
        layout = settings.template
        if layout.sheet_name not in records_by_sheet:
            raise ValueError(f"{layout.sheet_name}: 데이터 조건을 다시 읽은 뒤 미리보기를 확인해 주세요.")
        records = records_by_sheet[layout.sheet_name]
        try:
            values = renderer.prepare_template_records(records, layout, allow_empty=True, template=source)
            _, _, rows, clear, assignments = values
            summary = renderer.describe_template_preview(records, layout, rows)
        except ValueError as error:
            raise ValueError(f"{layout.sheet_name}: {error}") from error
        count = len(rows)
        if not count and config.empty_policy == "error":
            raise ValueError(f"{layout.sheet_name}: 조건에 맞는 자료가 없어 전체 내보내기를 중단했습니다.")
        action = "fill" if count else config.empty_policy
        changed_cells += len(clear | assignments.keys()) if action != "keep" else 0
        if changed_cells > MAX_WORKBOOK_CHANGED_CELLS:
            raise ValueError("한 파일에서 변경할 셀이 너무 많습니다. 시트나 자료 범위를 줄여 주세요.")
        summary.update(sheet_name=layout.sheet_name, kind=layout.kind, orientation=layout.orientation,
                       record_count=count, action=action, site=_scope(settings)["site"],
                       line=settings.line, production_area=settings.production_area)
        if isinstance(records, dict) and "_scope" in records:
            summary["scope"] = records["_scope"]
        summaries.append(summary)
        prepared.append((settings, values, action))
    return source, tuple(prepared), tuple(summaries)


def preview_workbook_template(records_by_sheet, config):
    """저장과 같은 검증으로 각 시트의 범위·값·빈 자료 처리를 확인한다."""
    _, _, summaries = _prepare_workbook(records_by_sheet, config)
    empty = tuple(summary["sheet_name"] for summary in summaries if not summary["record_count"])
    messages = tuple(
        f"{name}: 연결한 입력 영역을 비웁니다." if config.empty_policy == "clear"
        else f"{name}: 양식의 기존 내용을 유지합니다." for name in empty)
    return {"sheets": summaries, "empty_sheets": empty,
            "record_count": sum(summary["record_count"] for summary in summaries), "messages": messages}


def save_workbook_template_export(storage, path, config, *, production_area=None, batch_ids=None):
    """모든 시트를 먼저 검증한 뒤 원본과 다른 파일에 한 번에 저장한다."""
    path = Path(path)
    try:
        validate_workbook_config(config)
        if path.suffix.lower() != ".xlsx":
            raise ValueError("맞춤 출력 파일 이름은 .xlsx로 끝나야 합니다.")
        source_path = Path(config.path)
        if (source_path.resolve() == path.resolve()
                or path.exists() and os.path.samefile(source_path, path)):
            raise ValueError("원본 양식에는 저장할 수 없습니다. 다른 출력 파일 이름을 선택해 주세요.")
        records = read_workbook_records(storage, config, production_area=production_area, batch_ids=batch_ids)
        source, prepared, summaries = _prepare_workbook(records, config)
        # 한 시트라도 검증에 실패하면 출력 파일에는 아직 아무것도 쓰지 않았다.
        replacements = {}
        for settings, values, action in prepared:
            if action == "keep":
                continue
            _, sheet, _, clear, assignments = values
            replacements[source.sheets[settings.template.sheet_name]] = renderer.render_template_sheet(
                source, sheet, settings.template, clear, assignments)
        renderer.write_template_package(source, path, replacements)
        count = sum(summary["record_count"] for summary in summaries)
        empty = [summary["sheet_name"] for summary in summaries if not summary["record_count"]]
        message = f"{len(summaries)}개 시트 · {count:,}건 · 시트별 조건으로 저장했습니다."
        if empty:
            action = "연결한 입력 영역을 비웠습니다" if config.empty_policy == "clear" else "기존 내용을 유지했습니다"
            message += f" 자료 없는 시트({', '.join(empty)}): {action}."
        return ExportResult(ExportStatus.SAVED, path, message)
    except DatabaseError as error:
        return ExportResult(ExportStatus.FAILED, path, f"DB 자료를 읽지 못했습니다: {error}")
    except PermissionError:
        return ExportResult(ExportStatus.FAILED, path, "파일 저장 권한과 Excel에서 출력 파일이 열려 있는지 확인해 주세요.")
    except (OSError, ValueError, BadZipFile, etree.LxmlError) as error:
        return ExportResult(ExportStatus.FAILED, path, f"시트별 양식으로 저장하지 못했습니다: {error}")
