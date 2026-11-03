"""정상 저장한 선택값·정확한 버전 원문이 릴리즈 재판정을 막지 않는지 검증한다."""

from datetime import date

import pytest

from setron.features.equipment.schemas import SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ReleaseInput
from setron.features.version.schemas import CompareStatus

from test_release_revision import all_rows, release, revision_storage


def save_observation(storage, *, code="EQ-OPTIONAL", raw="v1", line=None):
    """실제 현황 저장 서비스로 선택 Line과 원문 SW를 등록한다.

    param
    - storage: 검증용 업무 저장소.
    - code, raw, line: 고유 호기·설치 버전 원문과 선택 Line. 빈 Line도 원문대로 저장.

    return
    - 저장·조회한 한 날짜의 StoredSnapshot.
    """
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})
    value = SnapshotInput(equipment_code=code, base_date=date(2026, 10, 1), site="Site A",
        model_name="O2", group_id=1, line=line, batch_id=batch["batch_id"],
        software=(SoftwareInput(family_id=1, version_raw=raw),))
    return EquipmentService(storage).save_snapshots((value,))[0]


@pytest.mark.parametrize("line", [None, "", "   "])
def test_saved_optional_line_does_not_block_unrelated_release(revision_storage, line):
    """저장 서비스가 허용한 Line 때문에 다른 계열의 릴리즈 등록이 취소되지 않는지 확인한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    - line: 저장 시 허용하는 선택 Line 원문.
    """
    storage = revision_storage
    saved = save_observation(storage, line=line)
    before = {name: all_rows(storage, name) for name in
              ("equipment_snapshot", "equipment_version", "import_batch")}
    result = ReleaseRevisionService(storage).register_release(
        ReleaseInput(family_id=2, version_raw="c3v1", version_order=1))
    assert saved.line == line
    assert result.value.release.current["family_id"] == 2
    assert result.refresh.dates[0].summary.equipment[0].record.line == line
    assert all_rows(storage, "equipment_snapshot") == before["equipment_snapshot"]
    assert all_rows(storage, "equipment_version") == before["equipment_version"]
    assert all_rows(storage, "import_batch")[:len(before["import_batch"])] == before["import_batch"]


def test_exact_whitespace_distinct_releases_do_not_block_unrelated_family(revision_storage):
    """DB가 구분한 두 버전 원문이 다른 계열의 변경까지 막지 않는지 확인한다.

    param
    - revision_storage: 정확한 원문 고유키를 사용하는 업무 저장소.
    """
    storage = revision_storage
    plain = release(storage, raw="v1", order=1)
    padded = release(storage, raw="v1 ", order=2)
    before = all_rows(storage, "release_version")
    result = ReleaseRevisionService(storage).register_release(
        ReleaseInput(family_id=2, version_raw="c3v1", version_order=1))
    family = result.refresh.families[1]
    assert family.match("v1").release_version_id == plain
    assert family.match("v1 ").release_version_id == padded
    assert family.match(" v1") is None
    assert [version.release_version_id for version in family.sorted.ordered] == [plain, padded]
    assert all_rows(storage, "release_version")[:len(before)] == before


def test_same_family_comparison_preserves_exact_raws_and_only_confirmed_aliases(revision_storage):
    """공백 차이를 합치지 않고 정확한 원문·확정 별칭만 연결해 같은 계열에서 비교하는지 확인한다.

    param
    - revision_storage: 실제 현황·릴리즈 저장과 재판정을 실행할 업무 저장소.
    """
    storage = revision_storage
    plain = release(storage, raw="v1", order=1)
    padded = release(storage, raw="v1 ", order=2)
    for code, raw in (("EQ-PLAIN", "v1"), ("EQ-PADDED", "v1 "),
                      ("EQ-UNCONFIRMED", " v1"), ("EQ-ALIAS", "v1 (H)")):
        save_observation(storage, code=code, raw=raw)
    context = RefreshContext(targets={(1, 1): padded}, applicability={(1, 1): (plain, padded)},
                             aliases={1: {"v1 (H)": "v1 "}})
    result = ReleaseRevisionService(storage, context).register_release(
        ReleaseInput(family_id=1, version_raw="v3", version_order=3))
    comparisons = {entry.record.equipment_code: entry.families[0].pms[0].comparison
                   for entry in result.refresh.dates[0].summary.equipment}
    assert comparisons["EQ-PLAIN"].release.release_version_id == plain
    assert comparisons["EQ-PLAIN"].status is CompareStatus.BELOW
    assert comparisons["EQ-PADDED"].release.release_version_id == padded
    assert comparisons["EQ-PADDED"].status is CompareStatus.AT_OR_ABOVE
    assert comparisons["EQ-UNCONFIRMED"].release is None
    assert comparisons["EQ-UNCONFIRMED"].status is CompareStatus.UNDETERMINED
    assert comparisons["EQ-ALIAS"].release.release_version_id == padded
    assert comparisons["EQ-ALIAS"].status is CompareStatus.AT_OR_ABOVE
    records = {row["equipment_code"]: row["equipment_id"] for row in all_rows(storage, "equipment")}
    snapshots = {row["snapshot_id"]: row["equipment_id"] for row in all_rows(storage, "equipment_snapshot")}
    versions = {snapshots[row["snapshot_id"]]: (row["version_raw"], row["release_version_id"])
                for row in all_rows(storage, "equipment_version")}
    assert versions[records["EQ-PLAIN"]] == ("v1", plain)
    assert versions[records["EQ-PADDED"]] == ("v1 ", padded)
    assert versions[records["EQ-UNCONFIRMED"]] == (" v1", None)
    assert versions[records["EQ-ALIAS"]] == ("v1 (H)", padded)
