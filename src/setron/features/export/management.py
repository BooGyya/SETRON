"""저장된 설비현황표와 릴리즈노트를 Excel(xlsx) 두 시트로 내보낸다.

현황 보고서(:mod:`setron.features.export.service`)와 달리 계산 결과가 아니라 DB에 저장된 **현재 관리값**을
설비현황표·릴리즈노트 시트로 옮긴다. 릴리즈노트에는 버전별 Item도 함께 담는다.

정한 규칙
- 모든 표를 한 트랜잭션에서 읽어 같은 시점의 자료를 쓴다.
- ID 대신 모델·설비군·버전 원문을 적고 SW계열 열은 노출하지 않는다. 원본 셀 위치·원문 셀 값은 넣지 않는다
  (원문까지 옮기려면 DB 전체 SQL 내보내기를 쓴다).
- `=`·`+`·`-`·`@`로 시작하는 글자는 Excel이 수식으로 읽지 않도록 글자 그대로 저장한다.
- 원본 Excel 양식·서식은 복원하지 않는다 (REQ-023 비고).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from setron.features.export.management_files import read_management_data, select_files
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.export.xlsx_cells import MAX_CELL_CHARACTERS, MAX_ROWS, write_literal_cell
from setron.features.item.original import jira_by_item, original_item_rows_from_batches
from setron.features.management_format import EQUIPMENT_HEADERS, RELEASE_HEADERS, equipment_group_name
from setron.features.production_area import production_area_label
from setron.features.reference_data import FOUNDRY_FAMILY_MODELS, strip_foundry
from setron.infrastructure.database import DatabaseError

SHEETS = (
    ("설비현황표", "기준일별 설비 현황. PM·적용 SW마다 한 줄"),
    ("릴리즈노트", "버전 계열별 릴리즈와 Item 적용 상태·모델 범위"),
)
STATE_LABELS = {"APPLIED": "적용", "NOT_APPLIED": "미적용", "UNKNOWN": "미확인"}
SCOPE_LABELS = {"ALL": "전체 모델", "LISTED": "지정 모델", "UNKNOWN": "미확인"}
HEADERS = {"설비현황표": EQUIPMENT_HEADERS, "릴리즈노트": RELEASE_HEADERS}


def build_management_workbook(storage, *, now=datetime.now, production_area=None, batch_ids=None) -> Workbook:
    """같은 시점의 DB 자료로 설비현황표·릴리즈노트 통합문서를 만든다."""
    records = read_management_records(storage, production_area=production_area, batch_ids=batch_ids)
    book = Workbook()
    book.properties.created = now()
    book.active.title = "설비현황표"
    book.create_sheet("릴리즈노트")
    for title, _ in SHEETS:
        header = HEADERS[title]
        _rows(book[title], header, (tuple(row[column] for column in header) for row in records[title]))
    return book


def read_management_records(storage, *, production_area=None, batch_ids=None) -> dict[str, Any]:
    """기본 출력과 맞춤 양식이 공유할 현재 관리값을 표준 항목명별 행으로 읽는다.

    모든 행은 같은 읽기 트랜잭션의 값이며, 원본 DB와 분리한 사전으로 반환한다.
    한 현황의 각 PM·SW는 별도 행이고 미확인 SW의 PM도 해당 현황에만 포함한다.
    """
    data = read_management_data(storage)
    names = _Names(data)
    selected = select_files(data, batch_ids)
    return _records_from_data(selected, names, production_area=production_area, choices_data=data)


def read_management_records_by_scope(storage, scopes, *, production_area=None, batch_ids=None):
    """모든 시트의 현재 자료를 한 번의 DB 읽기 스냅샷에서 분리한다.

    시트 이름은 분류로 해석하지 않는다. 사업장·라인은 저장된 원문과 정확히
    비교하며, 라인/사업장별 릴리즈는 해당 현황에 연결된 계열로만 제한한다.
    바깥 생산 영역과 시트의 생산 영역은 교집합이므로 서로 다르면 빈 결과다.
    """
    if not isinstance(scopes, Mapping) or not scopes:
        raise ValueError("내보낼 시트의 데이터 조건이 필요합니다.")
    data = read_management_data(storage)
    _validate_area(data, production_area)
    names = _Names(data)
    selected_data = select_files(data, batch_ids)
    result = {}
    for sheet, scope in scopes.items():
        if not isinstance(sheet, str) or not sheet or not isinstance(scope, Mapping):
            raise ValueError("시트 이름과 데이터 조건을 확인해 주세요.")
        if set(scope) - {"site", "line", "production_area"}:
            raise ValueError("지원하지 않는 시트 데이터 조건이 있습니다.")
        site, line, area = (scope.get(field) for field in ("site", "line", "production_area"))
        if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in (site, line)):
            raise ValueError("사업장과 라인을 입력하거나 ‘전체’를 명시적으로 선택해 주세요.")
        _validate_area(data, area)
        conflict = production_area is not None and area is not None and production_area != area
        selected = production_area if area is None else area
        result[sheet] = _records_from_data(selected_data, names, production_area=selected, site=site,
                                          line=line, empty=conflict, eligibility_data=data, choices_data=data)
        result[sheet]["_scope"] = {"site": site, "line": line, "production_area": selected,
                                    "global_production_area": production_area, "conflicting_area": conflict}
    return result


def _validate_area(data, production_area):
    if production_area is not None and (not isinstance(production_area, str)
                                       or production_area not in data["_hierarchy_areas"]):
        raise ValueError("내보낼 생산 분류를 등록된 목록에서 선택해 주세요.")


def _scope_choices(data):
    snapshots = data["equipment_snapshot"]
    sites = tuple(sorted({row["site"] for row in snapshots if row.get("site")}))
    return {"sites": sites,
            "lines": tuple(sorted({row["line"] for row in snapshots if row.get("line")})),
            "lines_by_site": {site: tuple(sorted({row["line"] for row in snapshots
                                                   if row.get("site") == site and row.get("line")})) for site in sites},
            "production_areas": dict(data["_hierarchy_areas"])}


def _records_from_data(data, names, *, production_area=None, site=None, line=None, empty=False,
                       eligibility_data=None, choices_data=None):
    _validate_area(data, production_area)

    def eligible(row):
        return (not empty and (production_area is None or row["production_area"] == production_area)
                and (site is None or row["site"] == site)
                and (line is None or (row["line"] or "") == line))

    snapshots = [row for row in data["equipment_snapshot"] if eligible(row)]
    equipment = _equipment_rows(data, names, snapshots)
    families = None
    located = site is not None or line is not None
    if production_area is not None or located or empty:
        # 선택한 릴리즈 파일은 설비 입력 작업을 포함하지 않아도 된다. 위치에 쓰이는
        # 계열은 현재 DB 설비 전체로 찾고, 출력할 노트는 파일 선택 결과와 교차한다.
        eligible_data = data if eligibility_data is None else eligibility_data
        snapshot_ids = {row["snapshot_id"] for row in eligible_data["equipment_snapshot"] if eligible(row)}
        families = {row["family_id"] for row in eligible_data["equipment_version"] if row["snapshot_id"] in snapshot_ids}
        if production_area is not None and not located and not empty:
            families.update(row["family_id"] for row in data["version_family"]
                            if names.area(row["family_id"]) == production_area)
    releases, identities = _release_rows(data, names, families, production_area=production_area,
                                        include_unlinked=production_area is None and not located and not empty)
    return {"설비현황표": tuple(dict(zip(EQUIPMENT_HEADERS, row, strict=True)) for row in equipment),
            "릴리즈노트": tuple({**dict(zip(RELEASE_HEADERS, row, strict=True)), **identity}
                               for row, identity in zip(releases, identities, strict=True)),
            "_scope_choices": _scope_choices(data if choices_data is None else choices_data)}


def save_management_summary(storage, path: str | Path, *, production_area=None, batch_ids=None) -> ExportResult:
    """관리 데이터를 xlsx 파일로 저장한다. 실패해도 예외 대신 원인을 담은 결과를 돌려준다."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .xlsx로 끝나야 합니다.")
    book, temporary = None, None
    try:
        book = build_management_workbook(storage, production_area=production_area, batch_ids=batch_ids)
        counts = {title: book[title].max_row - 1 for title, _ in SHEETS}
        if not any(counts.values()) and (production_area is not None or batch_ids is not None):
            if production_area is not None:
                label = production_area_label(production_area)
                message = (f"{label} 조건에 맞는 현재 저장 자료가 없습니다. "
                           "분류를 ‘전체’로 바꾸거나 해당 분류의 자료가 있는 파일을 선택해 주세요. "
                           "분류가 비어 있는 자료는 ‘전체’에서 내보낼 수 있습니다.")
            else:
                message = "선택한 파일에 현재 저장된 자료가 없습니다. 파일 목록을 새로고침한 뒤 다시 선택해 주세요."
            return ExportResult(ExportStatus.FAILED, path, message)
        with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".xlsx", delete=False) as handle:
            temporary = Path(handle.name)
        book.save(temporary)
        os.replace(temporary, path)
    except DatabaseError as error:
        return ExportResult(ExportStatus.FAILED, path, f"DB 자료를 읽지 못했습니다: {error}")
    except PermissionError:
        message = "파일을 저장할 수 없습니다. 같은 이름의 파일이 Excel에서 열려 있는지, 폴더 쓰기 권한이 있는지 확인해 주세요."
        return ExportResult(ExportStatus.FAILED, path, message)
    except (OSError, ValueError) as error:
        return ExportResult(ExportStatus.FAILED, path, f"파일을 저장하지 못했습니다: {getattr(error, 'strerror', None) or error}")
    finally:
        if book is not None:
            book.close()
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return ExportResult(ExportStatus.SAVED, path, " · ".join(f"{t} {n}행" for t, n in counts.items()))


# --- 시트 -----------------------------------------------------------------------

def _equipment_rows(data, names: _Names, snapshots) -> list[list[Any]]:
    codes = {e["equipment_id"]: e["equipment_code"] for e in data["equipment"]}
    pms_by_version = {}
    for pm in data["equipment_pm"]:
        pms_by_version.setdefault((pm["snapshot_id"], pm["equipment_version_id"]), []).append(pm)
    versions = {}
    for version in data["equipment_version"]:
        versions.setdefault(version["snapshot_id"], []).append(version)
    rows = []
    for snapshot in snapshots:
        base = [codes[snapshot["equipment_id"]], snapshot["base_date"], snapshot["site"], snapshot["line"] or "",
                snapshot["model_name"], names.group(snapshot["group_id"]) if snapshot["group_id"] else "혼합·미확인",
                names.area_label(snapshot["production_area"]), snapshot["structure_type"] or "",
                snapshot["geometry_type"] or "", snapshot["modification_type"] or ""]
        snapshot_id = snapshot["snapshot_id"]

        def append(version, pm):
            version = version or {}
            family = names.families.get(version.get("family_id"), {})
            software = [version.get("version_raw") or "", names.release(version["release_version_id"])
                        if version.get("release_version_id") else "미등록"]
            pm_cells = ([pm["pm_name"], names.group(pm["group_id"]), pm["model_name_raw"] or ""] if pm
                        else ["", "", ""])
            model = pm_cells[1] if pm else base[5] if snapshot["group_id"] else base[4]
            rows.append([*base, *pm_cells, *software, snapshot["batch_id"], family.get("ui_type") or "",
                         equipment_group_name(model), snapshot.get("process_name") or ""])

        # SW가 알려진 PM과 미확인 PM을 분리하되 모두 해당 현황에만 연결한다.
        known = versions.get(snapshot_id, [])
        unknown_pms = pms_by_version.get((snapshot_id, None), [])
        for version in known:
            for pm in pms_by_version.get((snapshot_id, version["equipment_version_id"]), []) or [None]:
                append(version, pm)
        for pm in unknown_pms:
            append(None, pm)
        if not known and not unknown_pms:
            append(None, None)
    rows.sort(key=lambda row: (row[6], row[17], row[2], row[3], row[0], row[1], row[10]))
    return rows


def _release_rows(data, names: _Names, families=None, *, production_area=None, include_unlinked=True):
    items = {i["item_id"]: i for i in data["item"]}
    # 저장된 NULL도 현재값이다. 두 Jira 열 모두 없던 이전 DB만 원본 셀로 보완한다.
    legacy_items = [item for item in data["item"] if "jira_code" not in item and "jira_key" not in item]
    legacy_jira = jira_by_item(original_item_rows_from_batches(data["import_batch"], legacy_items),
                               (item["item_id"] for item in legacy_items))
    jira = {item_id: (item["jira_code"] or "") if "jira_code" in item else
            (item["jira_key"] or "") if "jira_key" in item else ", ".join(legacy_jira[item_id].codes)
            for item_id, item in items.items()}
    links_by_release = {}
    for link in data["release_version_item"]:
        links_by_release.setdefault(link["release_version_id"], []).append(link)
    linked = {link["item_id"] for link in data["release_version_item"]}
    rows, identities = [], []
    releases = sorted(data["release_version"], key=lambda r: (r["family_id"], r["version_order"] is None,
                                                            r["version_order"] or 0, r["release_version_id"]))
    for release in releases:
        if families is not None and release["family_id"] not in families:
            continue
        family = names.families[release["family_id"]]
        owner = names.family_owner(release["family_id"])
        area = names.area(release["family_id"])
        if area is None and production_area is not None:
            area = production_area  # 두 분류에서 함께 쓰는 노트는 선택한 관측 범위에 포함한다.
        extra = [family.get("ui_type") or "", family.get("structure_type") or "", names.area_label(area), equipment_group_name(owner)]
        base = [owner, release["version_raw"],
                release["version_suffix"] or "", release["released_at"] or "",
                "" if release["version_order"] is None else release["version_order"],
                "확인됨" if release["item_mapping_status"] == "VERIFIED" else "미확인",
                ", ".join(release["edited_fields"] or ()), release["batch_id"]]
        links = links_by_release.get(release["release_version_id"], [])
        if not links:
            rows.append([*base, "", "", "", "", "", "", *extra, ""])
            identities.append({"_item_id": None, "_family_id": release["family_id"],
                               "_release_version_id": release["release_version_id"]})
        for link in links:
            item = items[link["item_id"]]
            rows.append([*base, _item_no(item), item["content"],
                         STATE_LABELS.get(link["item_state"], link["item_state"]),
                         SCOPE_LABELS.get(link["target_scope"], link["target_scope"]),
                         ", ".join(names.group(g) for g in link["target_group_ids"] or ()),
                         ", ".join(item["edited_fields"] or ()), *extra, jira[item["item_id"]]])
            identities.append({"_item_id": item["item_id"], "_family_id": release["family_id"],
                               "_release_version_id": release["release_version_id"]})
    for item in data["item"]:
        if include_unlinked and item["item_id"] not in linked:
            rows.append(["", "", "", "", "", "", "", item["batch_id"],
                         _item_no(item), item["content"], "", "", "", ", ".join(item["edited_fields"] or ())]
                        + ["", "", "", "", jira[item["item_id"]]])
            identities.append({"_item_id": item["item_id"], "_family_id": None, "_release_version_id": None})
    return rows, identities


# --- 보조 -----------------------------------------------------------------------

class _Names:
    """ID를 사람이 읽는 이름으로 바꾼다. 없는 ID는 'ID n'으로 적는다."""

    def __init__(self, data) -> None:
        self.area_labels = data.get("_hierarchy_areas", {"MEMORY": "Memory", "FOUNDRY": "Foundry"})
        self.groups = {g["group_id"]: g for g in data["equipment_group"]}
        self.families = {f["family_id"]: f for f in data["version_family"]}
        self.releases = {r["release_version_id"]: r for r in data["release_version"]}
        snapshots = {row["snapshot_id"]: row for row in data["equipment_snapshot"]}
        self.areas = {}
        for version in data["equipment_version"]:
            area = snapshots[version["snapshot_id"]]["production_area"]
            if area:
                self.areas.setdefault(version["family_id"], set()).add(area)

    def group(self, group_id) -> str:
        return self.groups[group_id]["group_name"] if group_id in self.groups else f"모델 ID {group_id}"

    def area_label(self, area):
        return self.area_labels.get(area, area or "")

    def family(self, family_id) -> str:
        return self.families[family_id]["family_name"] if family_id in self.families else f"계열 ID {family_id}"

    def family_owner(self, family_id) -> str:
        family = self.families.get(family_id)
        return self.group(family["group_id"]) if family else ""

    def release(self, release_id) -> str:
        return self.releases[release_id]["version_raw"] if release_id in self.releases else f"릴리즈 ID {release_id}"

    def area(self, family_id) -> str | None:
        family = self.families.get(family_id, {})
        separate_foundry = any(value["group_id"] == family.get("group_id") and strip_foundry(value["family_name"])[1]
                               for value in self.families.values())
        if self.family_owner(family_id) in FOUNDRY_FAMILY_MODELS or separate_foundry:
            return "FOUNDRY" if strip_foundry(self.family(family_id))[1] else "MEMORY"
        # HPA 등 공통 노트를 메모리로 단정하지 않는다. 저장된 관측 분류가 하나일 때만 적는다.
        areas = self.areas.get(family_id, set())
        return next(iter(areas)) if len(areas) == 1 else None


def _item_no(item) -> str:
    return "" if item["item_no"] is None else str(item["item_no"])


def _rows(sheet: Worksheet, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    sheet.freeze_panes = "A2"
    for column, title in enumerate(header, start=1):
        sheet.cell(1, column, title).font = Font(bold=True)
    for row_index, row in enumerate(rows, start=2):
        if row_index > MAX_ROWS:
            raise ValueError(f"{sheet.title}: Excel의 행 한도를 초과합니다.")
        for column, value in enumerate(row, start=1):
            cell = sheet.cell(row_index, column)
            if isinstance(value, str):
                location = f"{sheet.title}!{cell.coordinate} ({header[column - 1]})"
                if len(value) > MAX_CELL_CHARACTERS:
                    raise ValueError(f"{location}: Excel 한 셀의 32,767자 한도를 넘었습니다. 원문을 줄이지 않고 내보내기를 중단했습니다.")
                if ILLEGAL_CHARACTERS_RE.search(value):
                    raise ValueError(f"{location}: Excel이 보존할 수 없는 제어 문자가 있습니다.")
                try:
                    value.encode("utf-8")
                except UnicodeError:
                    raise ValueError(f"{location}: UTF-8로 보존할 수 없는 문자가 있습니다.") from None
            if value == "":
                cell.value = ""
            else:
                write_literal_cell(cell, value)
    sheet.auto_filter.ref = sheet.dimensions
