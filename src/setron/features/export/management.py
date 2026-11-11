"""저장된 설비현황표와 릴리즈노트를 Excel(xlsx) 두 시트로 내보낸다.

현황 보고서(:mod:`setron.features.export.service`)와 달리 계산 결과가 아니라 DB에 저장된 **현재 관리값**을
설비현황표·릴리즈노트 시트로 옮긴다. 릴리즈노트에는 버전별 Item도 함께 담는다.

정한 규칙
- 모든 표를 한 트랜잭션에서 읽어 같은 시점의 자료를 쓴다.
- ID 대신 사람이 읽는 이름(모델·계열·버전 원문)을 함께 적는다. 원본 셀 위치·원문 셀 값은 넣지 않는다
  (원문까지 옮기려면 DB 전체 SQL 내보내기를 쓴다).
- `=`·`+`·`-`·`@`로 시작하는 글자는 Excel이 수식으로 읽지 않도록 글자 그대로 저장한다.
- 원본 Excel 양식·서식은 복원하지 않는다 (REQ-023 비고).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from setron.features.equipment.repository import EquipmentRepository
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.infrastructure.database import DatabaseError

SHEETS = (
    ("설비현황표", "기준일별 설비 현황. PM·적용 SW마다 한 줄"),
    ("릴리즈노트", "버전 계열별 릴리즈와 Item 적용 상태·모델 범위"),
)
STATE_LABELS = {"APPLIED": "적용", "NOT_APPLIED": "미적용", "UNKNOWN": "미확인"}
SCOPE_LABELS = {"ALL": "전체 모델", "LISTED": "지정 모델", "UNKNOWN": "미확인"}
FORMULA_PREFIXES = ("=", "+", "-", "@")


def build_management_workbook(storage, *, now=datetime.now) -> Workbook:
    """같은 시점의 DB 자료로 설비현황표·릴리즈노트 통합문서를 만든다."""
    rows = EquipmentRepository().rows
    with storage.transaction() as tx:
        data = {table: rows(tx, table) for table in (
            "equipment_group", "version_family", "equipment", "equipment_snapshot", "equipment_version",
            "equipment_pm", "release_version", "item", "release_version_item")}
    names = _Names(data)

    book = Workbook()
    book.properties.created = now()
    book.active.title = "설비현황표"
    book.create_sheet("릴리즈노트")
    _equipment(book["설비현황표"], data, names)
    _releases(book["릴리즈노트"], data, names)
    return book


def save_management_summary(storage, path: str | Path) -> ExportResult:
    """관리 데이터를 xlsx 파일로 저장한다. 실패해도 예외 대신 원인을 담은 결과를 돌려준다."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .xlsx로 끝나야 합니다.")
    try:
        book = build_management_workbook(storage)
        book.save(path)
    except DatabaseError as error:
        return ExportResult(ExportStatus.FAILED, path, f"DB 자료를 읽지 못했습니다: {error}")
    except PermissionError:
        message = "파일을 저장할 수 없습니다. 같은 이름의 파일이 Excel에서 열려 있는지, 폴더 쓰기 권한이 있는지 확인해 주세요."
        return ExportResult(ExportStatus.FAILED, path, message)
    except OSError as error:
        return ExportResult(ExportStatus.FAILED, path, f"파일을 저장하지 못했습니다: {error.strerror or error}")
    counts = {title: book[title].max_row - 1 for title, _ in SHEETS}
    return ExportResult(ExportStatus.SAVED, path, " · ".join(f"{t} {n}행" for t, n in counts.items()))


# --- 시트 -----------------------------------------------------------------------

def _equipment(sheet: Worksheet, data, names: _Names) -> None:
    codes = {e["equipment_id"]: e["equipment_code"] for e in data["equipment"]}
    pms_by_version = {}
    for pm in data["equipment_pm"]:
        pms_by_version.setdefault(pm["equipment_version_id"], []).append(pm)
    versions = {}
    for version in data["equipment_version"]:
        versions.setdefault(version["snapshot_id"], []).append(version)
    rows = []
    for snapshot in sorted(data["equipment_snapshot"], key=lambda s: (codes[s["equipment_id"]], s["base_date"])):
        base = [codes[snapshot["equipment_id"]], snapshot["base_date"], snapshot["site"], snapshot["line"] or "",
                snapshot["model_name"], names.group(snapshot["group_id"]) if snapshot["group_id"] else "혼합·미확인",
                snapshot["production_area"] or "미확인", snapshot["structure_type"] or "",
                snapshot["geometry_type"] or "", snapshot["modification_type"] or ""]
        for version in versions.get(snapshot["snapshot_id"], []):
            software = [names.family(version["family_id"]), version["version_raw"],
                        names.release(version["release_version_id"]) if version["release_version_id"] else "미등록"]
            for pm in pms_by_version.get(version["equipment_version_id"], []) or [None]:
                pm_cells = ([pm["pm_name"], names.group(pm["group_id"]), pm["model_name_raw"] or ""] if pm
                            else ["", "", ""])
                rows.append([*base, *pm_cells, *software, snapshot["batch_id"]])
    _rows(sheet, ["설비 호기", "기준일", "사업장", "라인", "모델명(원문)", "대표 모델", "생산 영역", "구조", "형상",
                  "개조 유형", "PM", "PM 모델", "PM 모델명(원문)", "SW 계열", "SW 버전(원문)", "연결 릴리즈",
                  "입력 작업"], rows)


def _releases(sheet: Worksheet, data, names: _Names) -> None:
    items = {i["item_id"]: i for i in data["item"]}
    links_by_release = {}
    for link in data["release_version_item"]:
        links_by_release.setdefault(link["release_version_id"], []).append(link)
    linked = set()
    rows = []
    releases = sorted(data["release_version"], key=lambda r: (r["family_id"], r["version_order"] is None,
                                                            r["version_order"] or 0, r["release_version_id"]))
    for release in releases:
        base = [names.family_owner(release["family_id"]), names.family(release["family_id"]), release["version_raw"],
                release["version_suffix"] or "", release["released_at"] or "미확인",
                "미확인" if release["version_order"] is None else release["version_order"],
                "확인됨" if release["item_mapping_status"] == "VERIFIED" else "미확인",
                ", ".join(release["edited_fields"] or ()), release["batch_id"]]
        links = links_by_release.get(release["release_version_id"], [])
        if not links:
            rows.append([*base, "", "", "", "", "", ""])
        for link in links:
            item = items[link["item_id"]]
            linked.add(item["item_id"])
            rows.append([*base, _item_no(item), item["content"],
                         STATE_LABELS.get(link["item_state"], link["item_state"]),
                         SCOPE_LABELS.get(link["target_scope"], link["target_scope"]),
                         ", ".join(names.group(g) for g in link["target_group_ids"] or ()),
                         ", ".join(item["edited_fields"] or ())])
    rows += [["", "", "연결 없음", "", "", "", "", "", i["batch_id"],
              _item_no(i), i["content"], "", "", "", ", ".join(i["edited_fields"] or ())]
             for i in data["item"] if i["item_id"] not in linked]
    _rows(sheet, ["관리 모델", "버전 계열", "버전(원문)", "접미사", "출시일", "확인된 순서", "Item 매핑",
                  "직접 수정 항목", "입력 작업", "Item 번호", "내용", "적용 상태", "모델 범위", "지정 모델",
                  "Item 직접 수정 항목"], rows)


# --- 보조 -----------------------------------------------------------------------

class _Names:
    """ID를 사람이 읽는 이름으로 바꾼다. 없는 ID는 'ID n'으로 적는다."""

    def __init__(self, data) -> None:
        self.groups = {g["group_id"]: g for g in data["equipment_group"]}
        self.families = {f["family_id"]: f for f in data["version_family"]}
        self.releases = {r["release_version_id"]: r for r in data["release_version"]}

    def group(self, group_id) -> str:
        return self.groups[group_id]["group_name"] if group_id in self.groups else f"모델 ID {group_id}"

    def family(self, family_id) -> str:
        return self.families[family_id]["family_name"] if family_id in self.families else f"계열 ID {family_id}"

    def family_owner(self, family_id) -> str:
        family = self.families.get(family_id)
        return self.group(family["group_id"]) if family else ""

    def release(self, release_id) -> str:
        return self.releases[release_id]["version_raw"] if release_id in self.releases else f"릴리즈 ID {release_id}"


def _item_no(item) -> str:
    return "" if item["item_no"] is None else str(item["item_no"])


def _rows(sheet: Worksheet, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    for column, title in enumerate(header, start=1):
        sheet.cell(1, column, title).font = Font(bold=True)
    for row_index, row in enumerate(rows, start=2):
        for column, value in enumerate(row, start=1):
            cell = sheet.cell(row_index, column, value)
            if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
                cell.data_type = "s"  # 수식으로 실행하지 않고 글자 그대로 보존
