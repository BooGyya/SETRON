"""Item별 O로 만든 N 후보를 MySQL에 보존하고 변경 후 다시 판정한다."""

import os

import pytest

from setron.features.item.schemas import ItemState
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ItemInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.release.shared_models import ModelItemMark, SharedNoteItemService
from setron.features.targets import TargetService

from test_schema_mysql import schema_database, storage


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                                reason="MySQL 전용 테스트 DB 설정이 필요합니다.")


def marks(root, values):
    columns = (("M-C4", "NormalC4"), ("M-C4 DFB", "DFB"), ("M-C4+", "DEX")) if root == "M-C4" else (
        ("M-O5", "O5"), ("M-O5 Prime", "Prime"))
    return tuple(ModelItemMark(root, name, ItemState.APPLIED if value == "O" else ItemState.NOT_APPLIED,
                               header, value, "Release List", f"{chr(71 + index)}2", f"{chr(71 + index)}3")
                 for index, ((name, header), value) in enumerate(zip(columns, values)))


def test_o_based_candidates_reload_and_revalidate_each_shared_model(storage):
    models, families = {}, {}
    with storage.transaction() as tx:
        for name, category, parent in (("M-C4", "POLY", None), ("M-C4 DFB", "POLY", "M-C4"),
                ("M-C4+", "POLY", "M-C4"), ("M-O5", "OXIDE", None), ("M-O5 Prime", "OXIDE", "M-O5")):
            models[name] = tx.insert("equipment_group", {"group_name": name, "category": category,
                                                         "parent_group_id": models.get(parent)})["group_id"]
        for owner in ("M-C4", "M-O5"):
            families[owner] = tx.insert("version_family", {"group_id": models[owner],
                                                           "family_name": owner, "ui_type": "SMART"})["family_id"]

    def create(root, raw, order, rows):
        detail = ReleaseService(storage).register_release(
            ReleaseInput(family_id=families[root], version_raw=raw, version_order=order),
            tuple(ItemInput(content=f"합성 Item {number}") for number in range(len(rows))),
        )
        rid = detail.release.current["release_version_id"]
        ids = tuple(value.item.current["item_id"] for value in detail.items)
        for item_id, values in zip(ids, rows):
            SharedNoteItemService(storage).save(rid, item_id, marks(root, values))
        return rid, ids

    c4, items = create("M-C4", "v99", 1, (("O", "X", "X"), ("X", "O", "O")))
    dex, _ = create("M-C4", "v1_DEX_n4", 2, (("X", None, "O"),))
    create("M-C4", "v999", 3, (("X", "", "X"),))
    prime, _ = create("M-O5", "prime-a1", 1, (("X", "O"),))
    o5, _ = create("M-O5", "o5-a2", 2, (("O", None),))
    pairs = tuple((models[name], families["M-C4" if name.startswith("M-C4") else "M-O5"]) for name in models)
    expected = dict(zip(pairs, (c4, c4, dex, o5, prime)))
    assert TargetService(storage).read(pairs).targets == expected
    assert tuple(c.release_id for c in TargetService(storage).read((pairs[2],)).pairs[0].candidates) == (c4, dex)

    # 같은 버전에서 DFB의 마지막 O만 사라져도 다른 모델의 후보는 그대로 유지한다.
    SharedNoteItemService(storage).save(c4, items[1], marks("M-C4", ("X", "X", "O")))
    rechecked = ReleaseRevisionService(storage, RefreshContext(targets=expected,
                                                               use_saved_applicability=True)).refresh()
    validity = {(value.model_id, value.family_id): value for value in rechecked.targets}
    assert not validity[pairs[1]].valid and validity[pairs[1]].selected_release_id == c4
    assert all(validity[key].valid for key in pairs if key != pairs[1])

    with pytest.raises(RuntimeError, match="전체 취소"):
        with storage.transaction() as tx:
            SharedNoteItemService(storage).save_in(tx, c4, items[0], marks("M-C4", ("X", "X", "X")))
            assert not TargetService(storage).read_in(tx, (pairs[0],), expected).pairs[0].selection_valid
            raise RuntimeError("전체 취소")
    assert TargetService(storage).read((pairs[0],), expected).pairs[0].selection_valid
