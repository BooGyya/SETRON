"""원본 시트별 PM 입력 작업을 유지하며 HPA 보완 묶음의 최종 구성을 검증한다."""

from dataclasses import replace

import pytest

from setron.features.equipment.read import read_equipment_record
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.service import ImportValidationService
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError
from setron.features.data_admin import WORKBOOK_KEY  # 11-10: 가져오기 원본에 파일 이름도 보관

from test_hpa_history_safety import NEW_DAY, OLD_DAY, _input
from test_hpa_merge_safety import HPA, NORMAL, _counts, hpa_storage  # noqa: F401
from test_import_validation import draft, row
from test_snapshot_reimport import save, snapshot, storage  # noqa: F401


def original(storage, batch_id):
    """입력 작업에 보관된 실제 원본을 읽는다."""
    with storage.transaction() as tx:
        return tx.get("import_batch", {"batch_id": batch_id})["raw_data"]


@pytest.mark.parametrize("initial_hpa", [False, True], ids=["일반에서HPA", "HPA에서일반"])
@pytest.mark.parametrize("reverse", [False, True], ids=["PM1먼저", "PM2먼저"])
@pytest.mark.parametrize("split", [False, True], ids=["한시트", "분리시트"])
@pytest.mark.parametrize("shared", [False, True], ids=["PM별다른SW", "같은SW공유"])
def test_full_pm_correction_is_independent_of_sheet_partition_and_order(hpa_storage, initial_hpa, reverse, split, shared):
    """같은 날 모든 PM의 분류 보완은 시트 분할·순서와 무관하며 원본은 각각 보존한다."""
    storage = hpa_storage
    old, target = (HPA, NORMAL) if initial_hpa else (NORMAL, HPA)
    save(storage, row(1, **old), row(2, pm_name="PM2", **old), request_id="original")
    before = snapshot(storage)
    old_raw = original(storage, before.batch_id)
    names = ("PM2", "PM1") if reverse else ("PM1", "PM2")
    versions = {name: "vSHARED.2_d5 " if shared else f"vFINAL.{name} " for name in names}
    rows = [row(index, pm_name=name, **{**target, "version_raw": versions[name]},
                line="보완 라인" if name == "PM1" else None) for index, name in enumerate(names, 1)]
    drafts = tuple(draft(item, sheet=name) for name, item in zip(names, rows)) if split else (draft(*rows, sheet="전체 PM"),)
    requests = ImportValidationService(storage).prepare(drafts, "full-correction")
    receipts = DatabaseImportSaver(storage).save_many(requests)
    equipment = EquipmentService(storage)
    saved = snapshot(storage)

    assert saved.snapshot_id == before.snapshot_id
    assert read_equipment_record(saved, {9}).is_hpa is not initial_hpa
    assert saved.production_area is None and saved.line == "보완 라인"
    assert len(saved.software) == (1 if shared else 2)
    assert {pm.pm_name: (pm.group_id, sw.version_raw) for sw in saved.software for pm in sw.pms} == {
        name: (1 if initial_hpa else 9, versions[name]) for name in names}
    by_name = {name: (drafts[index], receipts[index]) if split else (drafts[0], receipts[0])
               for index, name in enumerate(names)}
    for name, (source, receipt) in by_name.items():
        cells = equipment.get_snapshot_sources("E-1", OLD_DAY, pm_name=name)
        assert any(cell.field == "pm_name" and cell.value == name and cell.batch_id == receipt.batch_id for cell in cells)
        assert any(cell.field == "version_raw" and cell.value == versions[name] and cell.batch_id == receipt.batch_id
                   and cell.sheet == source.sheet.sheet_name for cell in cells)
        assert original(storage, receipt.batch_id) == {source.sheet.sheet_name: source.sheet.cells,
                                                       WORKBOOK_KEY: source.workbook_name}
    metadata_source, metadata_receipt = by_name["PM1"]
    assert saved.batch_id == metadata_receipt.batch_id
    assert any(cell.field == "line" and cell.value == "보완 라인" and cell.batch_id == metadata_receipt.batch_id
               and cell.sheet == metadata_source.sheet.sheet_name for cell in equipment.get_snapshot_sources("E-1", OLD_DAY))
    if shared and split:
        # 공유 SW 대표 출처와 개별 PM 출처의 입력 작업은 별도로 유지된다.
        assert saved.software[0].batch_id == receipts[0].batch_id
        assert {pm.batch_id for pm in saved.software[0].pms} == {receipt.batch_id for receipt in receipts}
    assert original(storage, before.batch_id) == old_raw


@pytest.mark.parametrize("initial_hpa", [False, True], ids=["일반에서HPA", "HPA에서일반"])
@pytest.mark.parametrize("reverse", [False, True], ids=["PM1먼저", "PM2먼저"])
def test_split_correction_with_untouched_opposite_pm_rolls_back_all_requests(hpa_storage, initial_hpa, reverse):
    """여러 시트 적용 후 남은 PM과 분류가 섞이면 앞선 정상 입력과 원본 작업까지 취소한다."""
    storage = hpa_storage
    old, target = (HPA, NORMAL) if initial_hpa else (NORMAL, HPA)
    save(storage, *(row(index, pm_name=f"PM{index}", **old) for index in range(1, 4)), request_id="original")
    before, before_counts = snapshot(storage), _counts(storage)
    old_raw = original(storage, before.batch_id)
    names = ("PM2", "PM1") if reverse else ("PM1", "PM2")
    drafts = (draft(row(equipment_code="BEFORE-MIX", **NORMAL), sheet="정상 설비"),
              *(draft(row(pm_name=name, **target), sheet=name) for name in names))
    requests = ImportValidationService(storage).prepare(drafts, "partial-correction")

    with pytest.raises(StorageValidationError, match="HPA와 일반 설비"):
        DatabaseImportSaver(storage).save_many(requests)

    assert _counts(storage) == before_counts
    assert snapshot(storage) == before and snapshot(storage, "BEFORE-MIX") is None
    assert original(storage, before.batch_id) == old_raw


@pytest.mark.parametrize("initial_hpa", [False, True], ids=["일반에서HPA", "HPA에서일반"])
@pytest.mark.parametrize("reverse", [False, True], ids=["이전날먼저", "다음날먼저"])
def test_batch_api_keeps_original_other_day_classification_guard(hpa_storage, initial_hpa, reverse):
    """새 API로 모든 날짜를 함께 바꿔도 쓰기 전의 다른 날짜 분류 충돌을 숨기지 않는다."""
    storage = hpa_storage
    equipment = EquipmentService(storage)
    before = equipment.save_snapshots(tuple(_input(storage, "ORIGINAL-HISTORY", day, initial_hpa, pms=("PM1", "PM2"))
                                           for day in (OLD_DAY, NEW_DAY)))
    changes = tuple(_input(storage, "ORIGINAL-HISTORY", day, not initial_hpa, pms=("PM1", "PM2"))
                    for day in ((NEW_DAY, OLD_DAY) if reverse else (OLD_DAY, NEW_DAY)))
    before_counts = _counts(storage)

    with pytest.raises(StorageValidationError, match="날짜별 분류가 다릅니다"):
        with storage.transaction() as tx:
            equipment.import_snapshot_batches_in(tx, tuple((change,) for change in changes))

    assert _counts(storage) == before_counts
    assert tuple(equipment.get_snapshot("ORIGINAL-HISTORY", day) for day in (OLD_DAY, NEW_DAY)) == before


@pytest.mark.parametrize("initial_hpa", [False, True], ids=["일반에서HPA", "HPA에서일반"])
def test_batch_api_returns_validated_final_snapshots_for_each_input_batch(hpa_storage, initial_hpa):
    """시트별 반환 결과도 중간 혼합 구성이 아니라 검사한 최종 현황을 가리킨다."""
    storage = hpa_storage
    equipment = EquipmentService(storage)
    equipment.save_snapshots((_input(storage, "DIRECT-BATCH", OLD_DAY, initial_hpa, pms=("PM1", "PM2")),))
    changes = tuple(_input(storage, "DIRECT-BATCH", OLD_DAY, not initial_hpa, pms=(name,)) for name in ("PM1", "PM2"))
    changes = tuple(replace(value, software=(replace(value.software[0], version_raw="vSHARED "),)) for value in changes)

    with storage.transaction() as tx:
        results = equipment.import_snapshot_batches_in(tx, tuple((value,) for value in changes))

    final = equipment.get_snapshot("DIRECT-BATCH", OLD_DAY)
    assert results == ((final,), (final,))
    assert read_equipment_record(final, {9}).is_hpa is not initial_hpa
    assert {pm.pm_name for sw in final.software for pm in sw.pms} == {"PM1", "PM2"}
    assert {pm.batch_id for sw in final.software for pm in sw.pms} == {value.batch_id for value in changes}


@pytest.mark.parametrize("reverse", [False, True], ids=["화성먼저", "평택먼저"])
def test_batch_api_keeps_site_fixed_for_new_equipment_across_source_batches(hpa_storage, reverse):
    """새 호기의 같은 날짜를 여러 원본으로 등록해도 사업장 충돌을 조용히 무시하지 않는다."""
    storage = hpa_storage
    equipment = EquipmentService(storage)
    first = _input(storage, "NEW-SITE-CONFLICT", OLD_DAY, False, pms=("PM1",))
    second = replace(_input(storage, "NEW-SITE-CONFLICT", OLD_DAY, False, pms=("PM2",)), site="평택")
    changes = (second, first) if reverse else (first, second)
    before_counts = _counts(storage)

    with pytest.raises(StorageValidationError, match="사업장은 변경할 수 없습니다"):
        with storage.transaction() as tx:
            equipment.import_snapshot_batches_in(tx, tuple((value,) for value in changes))

    assert _counts(storage) == before_counts
    assert equipment.get_snapshot("NEW-SITE-CONFLICT", OLD_DAY) is None
