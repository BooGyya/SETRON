"""최종 멘토링(11-16) — 데이터 가져오기 · 데이터 관리.

1. 대량 릴리즈노트를 읽으면 SW 계열(모델명) 칸이 모두 빨간색 → SW 계열은 필수가 아니다.
   - 같은 시트의 다른 행 · 파일 이름에서 모델 · 계열을 채우고, 계열이 정해지면 모델명이 없어도 된다.
   - 번역표에 없는 설비군도 그 설비군(개조형이면 기본형)의 계열로 정하고, SW 계열 칸이 비어도 저장한다.
2. 데이터 관리에서 DFB를 추가하면 인식이 안 됨 · M-C4 DFB 등록 오류 → 설비군 이름은 적힌 그대로 인식한다.
   - 'C4(DFB)' · 'C4 DFB' · 'M-C4(DFB)'는 등록 설비군 'M-C4 DFB'와 같다.
   - 표기만 다른 같은 설비군을 다시 추가하면 어느 설비군과 같은지 알려 준다.
"""

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.data_admin import DataAdminService
from setron.features.excel_import.repository import ImportValidationRepository
from setron.features.excel_import.schemas import ReviewRow
from setron.features.excel_import.service import _derive_release_context, build_save_request, has_errors, validate
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.reference_data import group_key
from setron.features.storage.validation import StorageValidationError

from test_import_validation import draft, row
from test_release_revision import all_rows, revision_storage  # noqa: F401

RELEASE = ExtractKind.RELEASE_NOTE


def _reference(storage, source):
    with storage.transaction() as tx:
        return ImportValidationRepository().load(tx, (source,))


def _errors(source, reference):
    return [issue for issue in validate(source, reference) if issue.blocks_save]


@pytest.fixture
def c4(revision_storage):  # noqa: F811
    """회사 DB처럼 직접 등록한 기본형 M-C4와 개조형 M-C4 DFB (번역표 이름과 다른 계열명 'M-C4')."""
    admin = DataAdminService(revision_storage)
    base = admin.create_group("M-C4", "POLY")
    dfb = admin.create_group("M-C4 DFB", "POLY", base.group_id)
    family_id = next(f.family_id for f in admin.release_families() if f.group_id == base.group_id)
    return revision_storage, base, dfb, family_id


@pytest.mark.parametrize("written", ["C4(DFB)", "C4 DFB", "M-C4(DFB)", "c4 dfb"])
def test_dfb_written_as_on_the_sheet_is_recognized_and_saved_without_sw_family(c4, written):
    storage, _, dfb, family_id = c4
    source = draft(row(group_name="POLY", model_name=written, family_name=None, equipment_code="ESDFB1",
                       version_raw="v4.56.6.42 _DFB"))
    reference = _reference(storage, source)
    assert not _errors(source, reference)            # 모델 · SW 계열 칸이 빨간색이 아니다
    request = build_save_request(source, f"dfb-{written}", reference)
    assert request.rows[0].family_id == family_id    # 개조형은 기본형 계열
    DatabaseImportSaver(storage).save_many((request,))
    assert all_rows(storage, "equipment_snapshot")[-1]["group_id"] == dfb.group_id
    assert all_rows(storage, "equipment_version")[-1]["family_id"] == family_id


def test_adding_the_same_group_under_another_spelling_says_which_group_it_is(c4):
    storage, base, _, _ = c4
    admin = DataAdminService(storage)
    with pytest.raises((ValueError, StorageValidationError), match="'M-C4 DFB'과\\(와\\) 같은 설비군"):
        admin.create_group("C4(DFB)", "POLY", base.group_id)
    with pytest.raises((ValueError, StorageValidationError), match="새로 추가하지 않아도"):
        admin.create_group("M-C4 DFB", "POLY", base.group_id)
    assert admin.create_group("M-C4 QA", "POLY", base.group_id).name == "M-C4 QA"   # 다른 이름은 그대로 추가


def test_group_key_ignores_prefix_spaces_and_brackets_but_keeps_plus():
    assert {group_key(n) for n in ("C4(DFB)", "C4 DFB", "M-C4(DFB)", "M-C4 DFB", "m - c4 dfb")} == {"c4dfb"}
    assert group_key("M-C4") != group_key("M-C4+")
    assert group_key("O2") != group_key("O2 Plus")


def _release_row(number, **values):
    """실제 추출처럼 빈 칸(모델명 · SW 계열 · 설비군)은 값도 원본 위치도 없는 릴리즈노트 행."""
    values = {"release_date": "2026-11-01", **values}
    return ReviewRow(number, values, {name: f"{chr(65 + i)}{number}" for i, name in enumerate(values)})


def _release_rows(**values):
    return (_release_row(1, version_raw="v9.1", item_no="1", content="첫 Item", **values),
            _release_row(2, version_raw="v9.1", item_no="2", content="둘째 Item", **values),
            _release_row(3, version_raw="v9.2", item_no="1", content="다음 버전 Item", **values))


def test_release_note_takes_model_from_file_name_and_saves_with_blank_sw_family(c4):
    storage, _, _, family_id = c4
    source = draft(*_release_rows(), book="P_PJT_M-C4_SW_정기_Release_List.xlsx", sheet="Release", kind=RELEASE)
    filled = _derive_release_context(source)
    assert {r.value("model_name") for r in filled.rows} == {"M-C4"}      # 파일 이름에서
    reference = _reference(storage, filled)
    assert not _errors(filled, reference)
    request = build_save_request(filled, "release-from-file-name", reference)
    assert {r.family_id for r in request.rows} == {family_id}
    DatabaseImportSaver(storage).save_many((request,))
    assert {r["family_id"] for r in all_rows(storage, "release_version")} == {family_id}


def test_release_note_fills_blank_rows_from_the_other_rows_of_the_sheet(c4):
    storage, _, _, family_id = c4
    first, _, third = _release_rows()
    second = _release_row(2, version_raw="v9.1", item_no="2", content="둘째 Item", family_name="M-C4")
    source = draft(first, second, third, book="정기 릴리즈.xlsx", kind=RELEASE)
    filled = _derive_release_context(source)
    assert {r.value("family_name") for r in filled.rows} == {"M-C4"}    # 한 행에만 적힌 계열을 빈 행에도
    assert {r.value("model_name") for r in filled.rows} == {None}       # 모델명은 끝까지 비어 있어도
    reference = _reference(storage, filled)
    assert not _errors(filled, reference)                               # 계열이 정해졌으니 모델명 오류가 아니다
    request = build_save_request(filled, "release-from-rows", reference)
    assert {r.family_id for r in request.rows} == {family_id}
    DatabaseImportSaver(storage).save_many((request,))
    assert {r["family_id"] for r in all_rows(storage, "release_version")} == {family_id}


def test_release_note_without_any_clue_still_asks_for_the_family(c4):
    storage, _, _, _ = c4
    source = draft(*_release_rows(), book="정기 릴리즈.xlsx", sheet="Sheet1", kind=RELEASE)
    filled = _derive_release_context(source)
    assert filled == source                                             # 근거 없이 지어내지 않는다
    assert has_errors(validate(filled, _reference(storage, filled)))
