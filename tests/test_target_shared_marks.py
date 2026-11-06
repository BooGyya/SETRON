"""공유 노트에서 모델별 유효 O가 하나라도 있는 버전만 N 후보로 제공한다."""

import pytest

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.release.shared_models import SharedNoteItemService, read_item_model_marks
from setron.features.targets import CandidateConfirmation, TargetService
from setron.infrastructure.excel_reader import SheetData

from test_release_revision import all_rows, revision_storage


@pytest.fixture
def catalog(revision_storage):
    storage = revision_storage
    models, families = {}, {}
    with storage.transaction() as tx:
        for name, category, parent in (
            ("M-C4", "POLY", None), ("M-C4 DFB", "POLY", "M-C4"),
            ("M-C4+", "POLY", "M-C4"), ("M-O5", "OXIDE", None),
            ("M-O5 Prime", "OXIDE", "M-O5"),
        ):
            models[name] = tx.insert("equipment_group", {
                "group_name": name, "category": category, "parent_group_id": models.get(parent),
            })["group_id"]
        for owner in ("M-C4", "M-O5"):
            families[owner] = tx.insert("version_family", {
                "group_id": models[owner], "family_name": f"공유 노트 시험 {owner}", "ui_type": "SMART",
            })["family_id"]
    return storage, models, families


def save_release(catalog, raw, order, rows, *, owner="M-C4", suffix=None):
    """확정된 표 구조의 합성 셀을 읽어 버전별 Item 표식을 실제 저장한다."""
    storage, models, families = catalog
    headers = ["NormalC4", "DFB", "DEX", "HW연관", "고객사연관"] if owner == "M-C4" else ["O5", "Prime", "HW연관"]
    detail = ReleaseService(storage).register_release(
        ReleaseInput(family_id=families[owner], version_raw=raw, version_order=order, version_suffix=suffix),
        tuple(ItemInput(content=f"합성 변경 {raw}/{number}", item_no=number)
              for number in range(1, len(rows) + 1)),
    )
    release_id = detail.release.current["release_version_id"]
    sheet = SheetData("공유 노트 합성 시험.xlsx", "Release List", 2, 7,
                      [headers, *[list(row) + [None] * (len(headers) - len(row)) for row in rows]])
    item_ids = tuple(item.item.current["item_id"] for item in detail.items)
    saved_marks = []
    for offset, item_id in enumerate(item_ids, 3):
        marks = read_item_model_marks(sheet, note_model=owner, header_row=2, item_row=offset)
        SharedNoteItemService(storage).save(release_id, item_id, marks)
        saved_marks.append(marks)
    return release_id, item_ids, tuple(saved_marks)


def pair(catalog, model, *, owner="M-C4"):
    return catalog[1][model], catalog[2][owner]


def candidates(catalog, model, *, owner="M-C4", selected=None):
    key = pair(catalog, model, owner=owner)
    targets = None if selected is None else {key: selected}
    return TargetService(catalog[0]).read((key,), targets).pairs[0]


def test_one_o_in_any_item_admits_the_same_release_for_each_c4_model(catalog):
    rid, _, marks = save_release(catalog, "v4.56.6.41_d5", 1, (
        ("O", "X", None), ("X", "O", ""), (None, "X", "O"),
    ))
    before = all_rows(catalog[0], "app_setting")
    for name in ("M-C4", "M-C4 DFB", "M-C4+"):
        result = candidates(catalog, name)
        assert [value.release_id for value in result.candidates] == [rid]
        assert result.recommended_release_id == result.selected_release_id == rid
        assert result.selection_valid and result.reason is None
    assert marks[0][0].header_address == "G2" and marks[0][0].mark_address == "G3"
    assert all_rows(catalog[0], "app_setting") == before  # N 계산은 원본이나 설정을 갱신하지 않음


def test_saved_lineage_limits_shared_o_candidates(catalog):
    base, _, _ = save_release(catalog, "v1", 1, (("O", "O", "O"),))
    dfb, _, _ = save_release(catalog, "v2_DFB", 2, (("O", "O", "O"),), suffix="DFB")
    storage = catalog[0]
    service = TargetService(storage)
    model_id, family_id = pair(catalog, "M-C4 DFB")
    confirmed = service.confirm(model_id, family_id, (
        CandidateConfirmation(dfb, "O 표식", "확인된 순서", source_text="합성 시험 원본"),
    ))
    assert [candidate.release_id for candidate in confirmed.candidates] == [base, dfb]

    key = {"setting_key": f"target_applicability.v1:{model_id}:{family_id}"}
    with storage.transaction() as tx:
        payload = tx.get("app_setting", key)["setting_value"]
        tx.update("app_setting", key, {"setting_value": {**payload, "version_lineage": "DFB"}})

    result = candidates(catalog, "M-C4 DFB", selected=base)
    assert [candidate.release_id for candidate in result.candidates] == [dfb]
    assert not result.selection_valid and "버전 줄기" in result.reason
    assert [candidate.release_id for candidate in candidates(catalog, "M-C4").candidates] == [base, dfb]

    reconfirmed = service.confirm(model_id, family_id, (
        CandidateConfirmation(dfb, "O 표식", "다시 확인한 순서", source_text="합성 시험 원본"),
    ))
    assert [candidate.release_id for candidate in reconfirmed.candidates] == [dfb]

    with storage.transaction() as tx:
        tx.update("app_setting", key, {"setting_value": {**payload, "version_lineage": "UNKNOWN"}})
    result = candidates(catalog, "M-C4 DFB")
    assert result.candidates == () and not result.applicability_confirmed


@pytest.mark.parametrize("non_applied", ["X", None, "", "   "])
def test_x_or_blank_only_excludes_release_and_ignores_non_model_o_columns(catalog, non_applied):
    rid, _, _ = save_release(catalog, "v1", 1, ((non_applied, non_applied, non_applied, "O", "O"),))
    for name in ("M-C4", "M-C4 DFB", "M-C4+"):
        result = candidates(catalog, name, selected=rid)
        assert not result.candidates and result.recommended_release_id is None
        assert result.selected_release_id == rid and not result.selection_valid and result.reason


def test_model_specific_latest_confirmed_order_and_explicit_selections_remain_independent(catalog):
    base, _, _ = save_release(catalog, "v99", 1, (("O", "X", "O"),))
    dfb, _, _ = save_release(catalog, "v2_DFB", 2, (("X", "O", "X"),))
    dex, _, _ = save_release(catalog, "v1_DEX_n4", 3, (("X", "X", "O"),))
    storage, _, families = catalog
    ReleaseService(storage).register_release(ReleaseInput(family_id=families["M-C4"], version_raw="unmarked_latest", version_order=20))
    keys = tuple(pair(catalog, name) for name in ("M-C4", "M-C4 DFB", "M-C4+"))
    initial = TargetService(storage).read(keys)
    assert initial.targets == dict(zip(keys, (base, dfb, dex)))
    chosen = {keys[0]: None, keys[1]: dfb, keys[2]: base}
    changed = TargetService(storage).read(keys, chosen)
    assert changed.targets == chosen
    assert changed.effective_targets == {keys[1]: dfb, keys[2]: base}
    assert initial.targets == dict(zip(keys, (base, dfb, dex)))


def test_o5_prime_uses_its_own_column_in_the_shared_o5_note(catalog):
    prime, _, _ = save_release(catalog, "o5-a1", 1, (("X", "O", "O"),), owner="M-O5")
    normal, _, _ = save_release(catalog, "o5-a2", 2, (("O", "X", "O"),), owner="M-O5")
    assert candidates(catalog, "M-O5 Prime", owner="M-O5").recommended_release_id == prime
    assert candidates(catalog, "M-O5", owner="M-O5").recommended_release_id == normal
    assert [value.release_id for value in candidates(catalog, "M-O5 Prime", owner="M-O5").candidates] == [prime]


@pytest.mark.parametrize("replacement", ["X", None, ""])
def test_last_o_removed_does_not_reappear_through_an_older_manual_confirmation(catalog, replacement):
    storage = catalog[0]
    rid, item_ids, _ = save_release(catalog, "v1_DEX", 1, (("X", "X", "O"),))
    key = pair(catalog, "M-C4+")
    TargetService(storage).confirm(*key, (CandidateConfirmation(
        rid, "이전 수동 O 확인", "같은 분기 순서 확인", source_text="합성 시험 이전 확인",
    ),))
    sheet = SheetData("합성.xlsx", "Release List", 2, 7, [["NormalC4", "DFB", "DEX"], ["X", "X", replacement]])
    marks = read_item_model_marks(sheet, note_model="M-C4", header_row=2, item_row=3)
    SharedNoteItemService(storage).save(rid, item_ids[0], marks)
    result = candidates(catalog, "M-C4+", selected=rid)
    assert result.candidates == () and result.selected_release_id == rid
    assert not result.selection_valid and result.reason


@pytest.mark.parametrize("change", ["delete", "edit"])
def test_removed_or_modified_only_o_item_invalidates_existing_selected_release(catalog, change):
    storage = catalog[0]
    rid, item_ids, _ = save_release(catalog, "v1", 1, (("X", "O", "X"),))
    if change == "delete":
        with storage.transaction() as tx:
            tx.delete_release_item(item_ids[0])
    else:
        ReleaseService(storage).update_item(item_ids[0], {"content": "내용을 수정해 적용 근거 재확인 필요"})
    result = candidates(catalog, "M-C4 DFB", selected=rid)
    assert result.candidates == () and result.selected_release_id == rid
    assert not result.selection_valid and result.reason


def test_another_unchanged_o_item_keeps_release_eligible(catalog):
    storage = catalog[0]
    rid, item_ids, _ = save_release(catalog, "v1", 1, (("X", "O", "X"), (None, "O", None)))
    ReleaseService(storage).update_item(item_ids[0], {"content": "첫 항목의 근거 변경"})
    assert candidates(catalog, "M-C4 DFB", selected=rid).selection_valid
    ReleaseService(storage).update_item(item_ids[1], {"content": "남은 항목의 근거도 변경"})
    assert not candidates(catalog, "M-C4 DFB", selected=rid).selection_valid


@pytest.mark.parametrize("changes", [
    {"item_state": ItemState.NOT_APPLIED},
    {"target_scope": TargetScope.ALL},
])
def test_item_link_edit_invalidates_previous_o_evidence(catalog, changes):
    rid, item_ids, _ = save_release(catalog, "v1", 1, (("X", "O", "X"),))
    values = {"item_id": item_ids[0], "item_state": ItemState.APPLIED, "target_scope": TargetScope.UNKNOWN, **changes}
    ReleaseService(catalog[0]).save_item_links(rid, (ItemLinkInput(**values),))
    result = candidates(catalog, "M-C4 DFB", selected=rid)
    assert result.selected_release_id == rid and not result.selection_valid
    assert result.candidates == () and "적용 상태" in result.reason


def test_legacy_o_without_link_baseline_requires_reconfirmation(catalog):
    storage = catalog[0]
    rid, item_ids, _ = save_release(catalog, "v1", 1, (("X", "O", "X"),))
    setting = next(value for value in all_rows(storage, "app_setting")
                   if value["setting_key"] == f"shared_note_item_models.v1:{rid}:{item_ids[0]}")
    payload = dict(setting["setting_value"])
    payload.pop("link_fingerprint")
    with storage.transaction() as tx:
        tx.update("app_setting", {"setting_key": setting["setting_key"]}, {"setting_value": payload})
    result = candidates(catalog, "M-C4 DFB", selected=rid)
    assert not result.selection_valid and result.candidates == ()
    assert "확인 근거" in result.reason


def test_atomic_item_edit_returns_invalid_n_after_its_only_o_evidence_changes(catalog):
    storage = catalog[0]
    rid, item_ids, _ = save_release(catalog, "v1", 1, (("X", "O", "X"),))
    key = pair(catalog, "M-C4 DFB")
    chosen = TargetService(storage).read((key,))
    context = RefreshContext(targets=chosen.targets, applicability=chosen.applicability,
                             use_saved_applicability=True)
    result = ReleaseRevisionService(storage, context).update_item(item_ids[0], {"content": "검토 후 내용 변경"})
    assert result.refresh.targets[0].selected_release_id == rid
    assert not result.refresh.targets[0].valid and result.refresh.targets[0].reason


def test_o_does_not_supply_unknown_or_conflicting_release_order(catalog):
    good, _, _ = save_release(catalog, "v1", 1, (("X", "X", "O"),))
    unknown, _, _ = save_release(catalog, "unknown", None, (("X", "X", "O"),))
    first, _, _ = save_release(catalog, "conflict-a", 2, (("X", "X", "O"),))
    second, _, _ = save_release(catalog, "conflict-b", 2, (("X", "X", "O"),))
    result = candidates(catalog, "M-C4+")
    assert [value.release_id for value in result.candidates] == [good]
    assert {value.release_id for value in result.excluded} >= {unknown, first, second}


def test_outer_rollback_restores_the_previous_o_based_candidate(catalog):
    storage = catalog[0]
    rid, item_ids, _ = save_release(catalog, "v1", 1, (("X", "O", "X"),))
    key = pair(catalog, "M-C4 DFB")
    sheet = SheetData("합성.xlsx", "Release List", 2, 7, [["NormalC4", "DFB", "DEX"], ["X", "X", "X"]])
    marks = read_item_model_marks(sheet, note_model="M-C4", header_row=2, item_row=3)
    with pytest.raises(RuntimeError, match="전체 취소"):
        with storage.transaction() as tx:
            SharedNoteItemService(storage).save_in(tx, rid, item_ids[0], marks)
            assert not TargetService(storage).read_in(tx, (key,), {key: rid}).pairs[0].selection_valid
            raise RuntimeError("전체 취소")
    assert TargetService(storage).read((key,), {key: rid}).pairs[0].selection_valid
