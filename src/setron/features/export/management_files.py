"""가져오기 출처로 현재 관리값을 선택한다. 원본 셀이나 변경 이력에서 값을 복원하지 않는다."""

from dataclasses import dataclass
from datetime import datetime

from setron.features.data_admin import WORKBOOK_KEY, sheet_names
from setron.features.equipment.repository import EquipmentRepository


TABLES = ("equipment_group", "version_family", "equipment", "equipment_snapshot", "equipment_version",
          "equipment_pm", "release_version", "item", "release_version_item", "import_batch")


@dataclass(frozen=True)
class ImportedFile:
    batch_id: int
    filename: str
    kind: str
    imported_at: datetime | None
    sheets: tuple[str, ...] = ()


def read_management_data(storage):
    with storage.transaction() as tx:
        from setron.features.hierarchy import area_labels, category_labels
        return {**{name: EquipmentRepository().rows(tx, name) for name in TABLES},
                "_hierarchy_areas": area_labels(tx), "_hierarchy_categories": category_labels(tx)}


def origin_batches(row):
    """현재 행의 입력 작업과 병합된 원본 출처를 함께 확인한다. MANUAL 감사 기록은 따라가지 않는다."""
    result = {row["batch_id"]} if row.get("batch_id") is not None else set()
    for locations in (row.get("source_reference") or {}).values():
        for location in locations if isinstance(locations, list) else (locations,):
            if isinstance(location, dict) and type(location.get("batch_id")) is int:
                result.add(location["batch_id"])
    return result


def management_files(storage):
    """현재 관리값이 남아 있는 Excel 가져오기별 파일명·종류·일시를 읽는다."""
    data = read_management_data(storage)
    kinds = {}
    for tables, caption in ((("equipment_snapshot", "equipment_version", "equipment_pm"), "설비현황표"),
                            (("release_version", "item"), "릴리즈노트")):
        for table in tables:
            for row in data[table]:
                for batch_id in origin_batches(row):
                    kinds.setdefault(batch_id, set()).add(caption)
    result = []
    for batch in data["import_batch"]:
        if batch["source_type"] != "EXCEL" or batch["batch_id"] not in kinds:
            continue
        raw = batch.get("raw_data") or {}
        raw = raw if isinstance(raw, dict) else {}
        filename = raw.get(WORKBOOK_KEY)
        result.append(ImportedFile(batch["batch_id"], filename if isinstance(filename, str) and filename else "파일명 미기록",
                                   " · ".join(sorted(kinds[batch["batch_id"]])), batch.get("imported_at"), sheet_names(raw)))
    return tuple(sorted(result, key=lambda item: (item.imported_at or datetime.min, item.batch_id), reverse=True))


def select_files(data, batch_ids):
    """현재 출처 행과 표시할 부모를 고른다. 릴리즈 선택은 현재 연결 Item, Item 선택은 해당 연결만 포함한다."""
    if batch_ids is None:
        return data
    selected = frozenset(batch_ids)
    if not selected or any(type(value) is not int or value <= 0 for value in selected):
        raise ValueError("내보낼 파일을 하나 이상 선택해 주세요.")
    available = {row["batch_id"] for row in data["import_batch"] if row["source_type"] == "EXCEL"}
    if not selected <= available:
        raise ValueError("선택한 가져오기 기록이 없습니다. 파일 목록을 다시 불러와 주세요.")

    def belongs(row):
        return bool(origin_batches(row) & selected)

    pms = [row for row in data["equipment_pm"] if belongs(row)]
    pm_versions = {row["equipment_version_id"] for row in pms}
    all_pm_versions = {row["equipment_version_id"] for row in data["equipment_pm"]}
    versions = [row for row in data["equipment_version"] if row["equipment_version_id"] in pm_versions or
                (row["equipment_version_id"] not in all_pm_versions and belongs(row))]
    snapshot_ids = {row["snapshot_id"] for row in (*versions, *pms)}
    snapshots = [row for row in data["equipment_snapshot"] if row["snapshot_id"] in snapshot_ids or belongs(row)]

    release_ids = {row["release_version_id"] for row in data["release_version"] if belongs(row)}
    item_ids = {row["item_id"] for row in data["item"] if belongs(row)}
    links = [row for row in data["release_version_item"]
             if row["release_version_id"] in release_ids or row["item_id"] in item_ids]
    releases = release_ids | {row["release_version_id"] for row in links}
    items = item_ids | {row["item_id"] for row in links}
    return {**data, "equipment_snapshot": snapshots, "equipment_version": versions, "equipment_pm": pms,
            "release_version": [row for row in data["release_version"] if row["release_version_id"] in releases],
            "item": [row for row in data["item"] if row["item_id"] in items], "release_version_item": links}
