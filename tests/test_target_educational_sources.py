"""교육용 공유 노트의 누락 표식으로 Plus/Prime 적용을 추정하지 않는지 검증한다.

외부 교육용 XLSX는 읽기 전용이다. 이 파일의 버전·날짜는 업무 기준을 확정하는
자료가 아니며, 로컬 원본이 없는 환경에서는 XLSX를 읽는 두 사례만 건너뛴다.
"""

from datetime import date
from pathlib import Path
import re

import pytest

from setron.features.release.schemas import ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.targets import TargetService

from test_release_revision import all_rows, revision_storage


SOURCE_DIRECTORY = Path(__file__).resolve().parents[2] / "docs" / "04_데이터" / "가공"


@pytest.fixture
def educational_catalog(revision_storage):
    """교육용 표기의 원형·파생형 모델을 독립적인 검증 DB에 만든다."""
    storage = revision_storage
    models, families = {}, {}
    with storage.transaction() as tx:
        for name, category, parent in (
            ("M-C4", "POLY", None), ("M-C4 Plus", "POLY", "M-C4"),
            ("M-C4 DFB", "POLY", "M-C4"),
            ("M-O5", "OXIDE", None), ("M-O5 Prime", "OXIDE", "M-O5"),
        ):
            models[name] = tx.insert("equipment_group", {
                "group_name": name, "category": category,
                "parent_group_id": models.get(parent), "analysis_enabled": True,
            })["group_id"]
        for name in ("M-C4", "M-O5"):
            families[name] = tx.insert("version_family", {
                "group_id": models[name], "family_name": f"교육용 검증 {name}", "ui_type": "SMART",
            })["family_id"]
    return storage, models, families


def assert_unconfirmed_pair(pair):
    """후보 없음과 적용 미확인을 명시적으로 구분하는 반환 계약을 확인한다."""
    assert pair.candidates == ()
    assert pair.recommended_release_id is None
    assert pair.selected_release_id is None
    assert not pair.selection_valid and not pair.applicability_confirmed
    assert pair.reason and "근거" in pair.reason


def test_shared_note_names_suffixes_and_order_do_not_confirm_plus_or_prime(educational_catalog):
    """원본 XLSX 없이도 실행되는 일반 백엔드 회귀검증이다.

    버전 원문은 이 테스트에서만 만든 합성값이다. 원형 노트·파생 접미사·순서가
    있어도 확인된 모델 적용 목록이 없으면 모델별 후보로 자동 등록하지 않는다.
    """
    storage, models, families = educational_catalog
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})["batch_id"]
        notes = {name: tx.insert("release_note", {"group_id": models[name], "batch_id": batch})["release_note_id"]
                 for name in ("M-C4", "M-O5")}
    service = ReleaseService(storage)
    for owner, raw, suffix in (("M-C4", "v0.0.0_DEX", "_DEX"), ("M-O5", "v0.0.0_Prime", "Prime")):
        service.register_release(ReleaseInput(
            family_id=families[owner], version_raw=raw, version_suffix=suffix,
            release_note_id=notes[owner], version_order=1,
        ))
    requested = ((models["M-C4 Plus"], families["M-C4"]), (models["M-O5 Prime"], families["M-O5"]))
    catalog = TargetService(storage).read(requested, targets=None)
    assert tuple(pair.key for pair in catalog.pairs) == requested
    for pair in catalog.pairs:
        assert_unconfirmed_pair(pair)
    assert catalog.applicability == {} and catalog.effective_targets == {}
    assert all_rows(storage, "app_setting") == []


@pytest.mark.parametrize("owner,derived,cells,missing_pattern", [
    ("M-C4", "M-C4 Plus", ("A118", "A436", "A500"), r"DEX|Plus"),
    ("M-O5", "M-O5 Prime", ("A481", "A499"), r"Prime|프라임"),
])
def test_preserved_educational_sources_do_not_establish_missing_modified_model_scope(
    educational_catalog, owner, derived, cells, missing_pattern,
):
    """실제 교육용 셀을 읽되 표식 없는 파생형의 후보는 미확인으로 유지한다."""
    source = SOURCE_DIRECTORY / f"P_PJT_{owner}_SW_정기_Release_List_교육용.xlsx"
    if not source.is_file():
        pytest.skip(f"선택적 교육용 원본이 없습니다: {source.name}")
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.load_workbook(source, read_only=True, data_only=False)
    try:
        sheet = workbook["Release List"]
        text = tuple(value for row in sheet.iter_rows(values_only=True) for value in row if isinstance(value, str))
        assert not any(re.search(missing_pattern, value, re.IGNORECASE) for value in text)
        source_values = {cell: sheet[cell].value for cell in cells}
        if owner == "M-C4":
            assert (sheet["G2"].value, sheet["H2"].value) == ("NormalC4", "DFB")
            assert all(sheet[f"H{cell[1:]}"].value == "O" for cell in cells)
            source_values.update({"G2": "NormalC4", "H2": "DFB"})
        else:
            assert tuple(sheet.cell(3, number).value for number in range(1, 8)) == (
                "버전명", "No", "항목", "JIRA", "Update내용", "접수라인", "비고",
            )
        # B열 No는 각 버전 블록에서 1로 재시작한다. 릴리즈 순서로 사용하지 않는다.
        assert all(sheet[f"B{cell[1:]}"].value == 1 for cell in cells)
    finally:
        workbook.close()

    samples = []
    for cell in cells:
        match = re.fullmatch(r"(.+?)\s*\((\d{4}-\d{2}-\d{2})\)", source_values[cell])
        assert match is not None, f"교육용 버전·날짜 셀 형식이 바뀌었습니다: {cell}"
        samples.append((cell, match[1].rstrip(), date.fromisoformat(match[2])))
    # 이 사례에서는 C4의 명시적 DFB 분기 또는 O5의 연속 a1/a2 두 셀만 비교한다.
    # 교육용 동일 분기의 날짜 순서를 테스트 입력 순서로 명시하며 업무 순서를 확정하지 않는다.
    assert [sample[2] for sample in samples] == sorted(sample[2] for sample in samples)
    if owner == "M-C4":
        assert all(raw.endswith("_DFB") for _, raw, _ in samples)

    storage, models, families = educational_catalog
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {"Release List": source_values}})["batch_id"]
        note = tx.insert("release_note", {"group_id": models[owner], "batch_id": batch})["release_note_id"]
    releases = []
    for order, (cell, raw, released_at) in enumerate(samples, 1):
        detail = ReleaseService(storage).register_release(ReleaseInput(
            family_id=families[owner], version_raw=raw,
            version_suffix="_DFB" if owner == "M-C4" else None,
            released_at=released_at, version_order=order,
            batch_id=batch, release_note_id=note,
            source_reference={field: {"sheet": "Release List", "cells": [cell]}
                              for field in ("version_raw", "released_at", "version_order")},
        ))
        releases.append(detail.release.current["release_version_id"])
    requested = ((models["M-C4 Plus"], families["M-C4"]), (models["M-O5 Prime"], families["M-O5"]))
    target_service = TargetService(storage)
    catalog = target_service.read(requested, targets=None)
    for pair in catalog.pairs:
        assert_unconfirmed_pair(pair)
    assert catalog.applicability == {} and all_rows(storage, "app_setting") == []
    # 이름과 공유 노트만 보고 고른 N을 전달해도 유효한 후보로 승격하지 않는다.
    pair_key = models[derived], families[owner]
    explicit = target_service.read((pair_key,), targets={pair_key: releases[-1]}).pairs[0]
    assert explicit.selected_release_id == releases[-1]
    assert not explicit.selection_valid and explicit.candidates == () and explicit.reason


def test_preserved_c4_o_items_supply_model_candidates_and_latest_confirmed_branch(educational_catalog):
    """교육용 실제 O 셀에서 후보를 생성한다. 업무용 원본의 분기 순서를 확정하지 않는다."""
    source = SOURCE_DIRECTORY / "P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx"
    if not source.is_file():
        pytest.skip(f"선택적 교육용 원본이 없습니다: {source.name}")
    openpyxl = pytest.importorskip("openpyxl")
    from setron.features.release.schemas import ItemInput
    from setron.features.release.shared_models import SharedNoteItemService, read_item_model_marks
    from setron.infrastructure.excel_reader import SheetData, cell_address

    workbook = openpyxl.load_workbook(source, read_only=True, data_only=False)
    try:
        raw_rows = [list(row) for row in workbook["Release List"].iter_rows(values_only=True)]
    finally:
        workbook.close()
    width = max(map(len, raw_rows))
    values = [row + [None] * (width - len(row)) for row in raw_rows]
    assert (values[1][6], values[1][7]) == ("NormalC4", "DFB")
    sheet = SheetData(source.name, "Release List", 1, 1, values)
    starts = [number for number, row in enumerate(values, 1) if number >= 3 and row[0] is not None]
    samples = ((3, "M-C4", 1), (118, "M-C4 DFB", 1), (436, "M-C4 DFB", 2))
    selected_rows = {
        first: tuple(number for number in range(first, next((value for value in starts if value > first), len(values) + 1))
                     if type(values[number - 1][1]) is int)
        for first, _, _ in samples
    }
    # 이 교육용 사본의 A3 블록에는 DFB O가 없다. 첨부된 업무 화면의 값으로 채우지 않는다.
    assert not any(values[number - 1][7] == "O" for number in selected_rows[3])
    storage, models, families = educational_catalog
    with storage.transaction() as tx:
        dfb_family = tx.insert("version_family", {
            "group_id": models["M-C4 DFB"], "family_name": "교육용 검증 C4 DFB 분기", "ui_type": "SMART",
        })["family_id"]
        raw_cells = {"G2": values[1][6], "H2": values[1][7]}
        for rows in selected_rows.values():
            for number in rows:
                raw_cells.update({cell_address(number, column): value
                                  for column, value in enumerate(values[number - 1], 1)})
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {"Release List": raw_cells}})["batch_id"]
        note = tx.insert("release_note", {"group_id": models["M-C4"], "batch_id": batch})["release_note_id"]
    branch_families = {"M-C4": families["M-C4"], "M-C4 DFB": dfb_family}
    expected = {(models[name], family_id): []
                for family_id in branch_families.values() for name in ("M-C4", "M-C4 DFB")}
    saved = {}
    services = ReleaseService(storage), SharedNoteItemService(storage)
    dfb_dates = []
    for first, branch, order in samples:
        match = re.fullmatch(r"(.+?)\s*\((\d{4}-\d{2}-\d{2})\)", values[first - 1][0])
        assert match is not None
        raw, released_at = match[1].rstrip(), date.fromisoformat(match[2])
        if branch == "M-C4 DFB":
            assert raw.endswith("_DFB")
            dfb_dates.append(released_at)
        family_id = branch_families[branch]
        # 같은 교육용 DFB 분기의 A118→A436를 확인한 시험 입력이다.
        # 날짜만으로 전체 업무 릴리즈나 다른 분기 사이의 비교 순서를 추정하지 않는다.
        detail = services[0].register_release(ReleaseInput(
            family_id=family_id, version_raw=raw, released_at=released_at, version_order=order,
            batch_id=batch, release_note_id=note,
            source_reference={field: {"sheet": "Release List", "cells": [f"A{first}"]}
                              for field in ("version_raw", "released_at", "version_order")},
        ))
        release_id = detail.release.current["release_version_id"]
        saved[first] = release_id
        applied = set()
        for number in selected_rows[first]:
            item = services[0].register_item(release_id, ItemInput(
                item_no=values[number - 1][1], content=values[number - 1][3],
                batch_id=batch, release_note_id=note,
                source_reference={"item_no": {"sheet": "Release List", "cells": [f"B{number}"]},
                                  "content": {"sheet": "Release List", "cells": [f"D{number}"]}},
            ))
            marks = read_item_model_marks(sheet, note_model="M-C4", header_row=2, item_row=number)
            assert {mark.model_name for mark in marks} == {"M-C4", "M-C4 DFB"}
            services[1].save(release_id, item.current["item_id"], marks)
            applied.update(mark.model_name for mark in marks if mark.mark_raw == "O")
        for name in applied:
            expected[models[name], family_id].append(release_id)
    assert dfb_dates == sorted(dfb_dates)
    before = all_rows(storage, "app_setting")
    catalog = TargetService(storage).read(tuple(expected))
    for result in catalog.pairs:
        assert [candidate.release_id for candidate in result.candidates] == expected[result.key]
        assert result.recommended_release_id == (expected[result.key][-1] if expected[result.key] else None)
        for candidate in result.candidates:
            assert candidate.confirmation.source_reference["model_applicability"]["sheet"] == "Release List"
    assert catalog.targets[models["M-C4"], branch_families["M-C4"]] == saved[3]
    assert catalog.targets[models["M-C4 DFB"], dfb_family] == saved[436]
    absent_dex = TargetService(storage).read(((models["M-C4 Plus"], dfb_family),)).pairs[0]
    assert not absent_dex.candidates and not absent_dex.applicability_confirmed
    assert all_rows(storage, "app_setting") == before
