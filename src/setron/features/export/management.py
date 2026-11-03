"""관리 데이터 전체를 정리된 표의 Excel(xlsx)로 내보낸다 (명세 BE-021, REQ-023·REQ-063).

현황 보고서(:mod:`setron.features.export.service`)와 달리 계산 결과가 아니라 DB에 저장된 **현재 관리값**을
데이터 종류별 시트로 옮긴다. 앱에서 수정·저장한 값이 그대로 나온다.

정한 규칙
- 모든 표를 한 트랜잭션에서 읽어 같은 시점의 자료를 쓴다.
- ID 대신 사람이 읽는 이름(모델·계열·버전 원문)을 함께 적는다. 원본 셀 위치·원문 셀 값은 넣지 않는다
  (원문까지 옮기려면 DB 전체 SQL 내보내기를 쓴다).
- `=`·`+`·`-`·`@`로 시작하는 글자는 Excel이 수식으로 읽지 않도록 글자 그대로 저장한다.
- 원본 Excel 양식·서식은 복원하지 않는다 (REQ-023 비고).
"""

from __future__ import annotations

import json
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
    ("모델·계열", "설비군 분류·모델과 버전 계열"),
    ("설비 현황", "기준일별 설비 현황. PM·적용 SW마다 한 줄"),
    ("릴리즈", "버전 계열별 릴리즈 버전"),
    ("Item", "릴리즈 버전별 Item 적용 상태와 모델 범위"),
    ("위험 기준", "등록된 환경안전·결함·직접 지정 위험"),
    ("관리 설정", "노후도 기간 기준 등"),
)
CATEGORY_LABELS = {"POLY": "Poly", "OXIDE": "Oxide", "KCTC": "KCTC", "HPA": "HPA"}
STATE_LABELS = {"APPLIED": "적용", "NOT_APPLIED": "미적용", "UNKNOWN": "미확인"}
SCOPE_LABELS = {"ALL": "전체 모델", "LISTED": "지정 모델", "UNKNOWN": "미확인"}
RULE_LABELS = {"SAFETY_REQUIRED": "환경안전 필수 Item", "DEFECT_ITEM": "결함 Item", "DIRECT_VERSION": "직접 지정 버전"}
FORMULA_PREFIXES = ("=", "+", "-", "@")


def build_management_workbook(storage, *, now=datetime.now) -> Workbook:
    """DB의 관리 데이터 전체로 통합문서를 만든다. 파일로 저장하지는 않는다."""
    rows = EquipmentRepository().rows
    with storage.transaction() as tx:
        data = {table: rows(tx, table) for table in (
            "equipment_group", "version_family", "equipment", "equipment_snapshot", "equipment_version",
            "equipment_pm", "release_version", "item", "release_version_item", "risk_rule", "app_setting")}
    names = _Names(data)

    book = Workbook()
    guide = book.active
    guide.title = "안내"
    _rows(guide, ["항목", "내용"], [
        ["생성 시각", now().strftime("%Y-%m-%d %H:%M:%S")],
        ["내용", "DB에 저장된 현재 관리값 (앱에서 수정·저장한 값 반영)"],
        ["원본 셀", "원본 셀 위치·원문 값은 넣지 않았습니다. 원문까지 옮기려면 DB 전체 SQL 내보내기를 쓰세요."],
        *([f"시트: {title}", description] for title, description in SHEETS),
    ])
    for title, _ in SHEETS:
        book.create_sheet(title)
    _catalog(book["모델·계열"], data, names)
    _equipment(book["설비 현황"], data, names)
    _releases(book["릴리즈"], data, names)
    _items(book["Item"], data, names)
    _risks(book["위험 기준"], data, names)
    _rows(book["관리 설정"], ["설정 이름", "값"],
          [[r["setting_key"], json.dumps(r["setting_value"], ensure_ascii=False)] for r in data["app_setting"]])
    return book


def save_management_data(storage, path: str | Path) -> ExportResult:
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

def _catalog(sheet: Worksheet, data, names: _Names) -> None:
    families = {}
    for family in data["version_family"]:
        families.setdefault(family["group_id"], []).append(family)
    rows = []
    for group in data["equipment_group"]:
        base = [CATEGORY_LABELS.get(group["category"], group["category"]), group["group_name"],
                names.group(group["parent_group_id"]) if group["parent_group_id"] else "",
                "예" if group["analysis_enabled"] else "아니오 (분석 제외)"]
        for family in families.get(group["group_id"], []) or [None]:
            rows.append([*base, family["family_name"] if family else "", (family or {}).get("ui_type") or ""])
    _rows(sheet, ["분류", "모델", "기본 모델", "분석 포함", "버전 계열", "UI 유형"], rows)


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
    rows = [[names.family_owner(r["family_id"]), names.family(r["family_id"]), r["version_raw"],
             r["version_suffix"] or "", r["released_at"] or "미확인",
             "미확인" if r["version_order"] is None else r["version_order"],
             "확인됨" if r["item_mapping_status"] == "VERIFIED" else "미확인",
             ", ".join(r["edited_fields"] or ()), r["batch_id"]]
            for r in sorted(data["release_version"], key=lambda r: (r["family_id"], r["version_order"] is None,
                                                                    r["version_order"] or 0, r["release_version_id"]))]
    _rows(sheet, ["관리 모델", "버전 계열", "버전(원문)", "접미사", "출시일", "확인된 순서", "Item 매핑",
                  "직접 수정 항목", "입력 작업"], rows)


def _items(sheet: Worksheet, data, names: _Names) -> None:
    items = {i["item_id"]: i for i in data["item"]}
    linked = set()
    rows = []
    for link in data["release_version_item"]:
        item = items[link["item_id"]]
        linked.add(item["item_id"])
        release = names.releases[link["release_version_id"]]
        rows.append([names.family(release["family_id"]), release["version_raw"], _item_no(item), item["content"],
                     STATE_LABELS.get(link["item_state"], link["item_state"]),
                     SCOPE_LABELS.get(link["target_scope"], link["target_scope"]),
                     ", ".join(names.group(g) for g in link["target_group_ids"] or ()),
                     ", ".join(item["edited_fields"] or ())])
    rows += [["", "연결 없음", _item_no(i), i["content"], "", "", "", ", ".join(i["edited_fields"] or ())]
             for i in data["item"] if i["item_id"] not in linked]
    _rows(sheet, ["버전 계열", "버전", "Item 번호", "내용", "적용 상태", "모델 범위", "지정 모델", "직접 수정 항목"], rows)


def _risks(sheet: Worksheet, data, names: _Names) -> None:
    items = {i["item_id"]: i for i in data["item"]}

    def item(item_id):
        return f"{_item_no(items[item_id])} {items[item_id]['content']}".strip() if item_id in items else ""

    rows = [[RULE_LABELS.get(r["rule_type"], r["rule_type"]), names.family(r["family_id"]),
             names.group(r["group_id"]) if r["group_id"] else "계열 전체", item(r["item_id"]),
             *(names.release(r[key]) if r[key] else "" for key in
               ("release_version_id", "start_release_version_id", "end_release_version_id")),
             r["risk_reason"], "사용" if r["is_enabled"] else "사용 안 함",
             "확인됨" if r["mapping_status"] == "VERIFIED" else "연결 확인 필요"]
            for r in data["risk_rule"]]
    _rows(sheet, ["유형", "버전 계열", "대상 모델", "Item", "직접 지정 버전", "시작 버전(포함)", "해제 버전(제외)",
                  "사유", "사용 여부", "연결 상태"], rows)


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
