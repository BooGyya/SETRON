"""BE-011 조회·후보 저장·변경 후 재검증을 실제 MySQL 5.6.19에서 확인한다."""

import os
from datetime import date

import pytest

from setron.features.equipment.schemas import SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.query_options.catalog import QueryCatalogService
from setron.features.query_options.schemas import LineOption, QueryScope
from setron.features.release.schemas import ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.release.revision import ReleaseRevisionService
from setron.features.targets.schemas import CandidateConfirmation
from setron.features.targets.service import TargetService

from test_schema_mysql import schema_database, storage


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                                reason="MySQL 전용 테스트 DB 설정이 필요합니다.")


def test_mysql_query_scope_candidate_reload_and_release_invalidation(storage):
    early, late = date(2026, 10, 1), date(2026, 11, 1)
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})["batch_id"]
        base = tx.insert("equipment_group", {"group_name": "BE011 합성 원형", "category": "POLY"})["group_id"]
        derived = tx.insert("equipment_group", {"group_name": "BE011 합성 개조형", "category": "POLY",
                                                 "parent_group_id": base})["group_id"]
        family = tx.insert("version_family", {"group_id": base, "family_name": "BE011 합성 계열",
                                              "ui_type": "SMART"})["family_id"]
    releases = ReleaseService(storage)
    first = releases.register_release(ReleaseInput(family_id=family, version_raw="v99", version_order=1))
    second = releases.register_release(ReleaseInput(family_id=family, version_raw="v2_n4", version_order=2))
    low = first.release.current["release_version_id"]
    high = second.release.current["release_version_id"]
    EquipmentService(storage).save_snapshots(tuple(
        SnapshotInput(equipment_code=code, base_date=day, site=site, line=line, model_name="합성 시험",
                      group_id=model, batch_id=batch,
                      software=(SoftwareInput(family_id=family, version_raw="v99", release_version_id=low),))
        for code, day, site, line, model in (
            ("BE011-BASE", early, "화성", "L1", base),
            ("BE011-MOD", early, "평택", "L1", derived),
            ("BE011-BASE", late, "화성", "L2", derived),
        )))
    confirm = lambda rid: CandidateConfirmation(rid, "합성 모델별 적용 확인", "합성 분기 순서 확인",
                                                 source_text="BE-011 MySQL 통합 시험")
    TargetService(storage).confirm(base, family, (confirm(low), confirm(high)))
    TargetService(storage).confirm(derived, family, (confirm(low),))
    # 새 서비스로 다시 읽어 DB LONGTEXT/VARBINARY 왕복까지 검증한다.
    service = QueryCatalogService(storage)
    result = service.read(QueryScope(early))
    assert result.target_catalog.targets == {(base, family): high, (derived, family): low}
    narrow = service.read(QueryScope(early, sites=("화성", "평택"), site_lines=(LineOption("화성", "L1"),)),
                          result.target_catalog.targets)
    assert narrow.options.model_family_pairs == ((base, family),)
    assert narrow.target_catalog.targets == {(base, family): high}
    assert narrow.refresh_context.targets == result.target_catalog.targets
    assert service.read(QueryScope(late)).options.model_family_pairs == ((derived, family),)
    assert service.read(QueryScope(date(2026, 10, 2))).options.matches == ()
    selected = result.target_catalog.targets
    changed = ReleaseRevisionService(storage, narrow.refresh_context).update_release(high, {"version_suffix": "_DEX"})
    checked = {(value.model_id, value.family_id): value for value in changed.refresh.targets}
    assert not checked[base, family].valid and checked[derived, family].valid
    after = service.read(QueryScope(early), selected)
    validity = {pair.key: pair for pair in after.target_catalog.pairs}
    assert not validity[base, family].selection_valid
    assert validity[base, family].selected_release_id == high
    assert validity[derived, family].selection_valid
    assert validity[derived, family].selected_release_id == low
    with pytest.raises(RuntimeError, match="전체 취소"):
        with storage.transaction() as tx:
            TargetService(storage).confirm_in(tx, derived, family, ())
            raise RuntimeError("전체 취소")
    assert TargetService(storage).read(((derived, family),)).pairs[0].recommended_release_id == low


def test_mysql_optional_lines_remain_queryable_without_changing_their_raw_values(storage):
    day = date(2026, 11, 2)
    values = (None, "", "   ", "L1", " Line ")
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})["batch_id"]
        model = tx.insert("equipment_group", {"group_name": "BE011 라인 원문 시험", "category": "POLY"})["group_id"]
        family = tx.insert("version_family", {"group_id": model, "family_name": "BE011 라인 원문 시험"})["family_id"]
    EquipmentService(storage).save_snapshots(tuple(
        SnapshotInput(equipment_code=f"BE011-LINE-{index}", base_date=day, site="화성", line=value,
                      model_name="라인 원문 시험", group_id=model, batch_id=batch,
                      software=(SoftwareInput(family_id=family, version_raw="v1"),))
        for index, value in enumerate(values)
    ))
    service = QueryCatalogService(storage)
    options = service.read(QueryScope(day)).options
    assert set(options.lines) == {LineOption("화성", value) for value in values}
    for index, value in enumerate(values):
        expected = f"BE011-LINE-{index}"
        for scope in (QueryScope(day, lines=(value,)), QueryScope(day, site_lines=(LineOption("화성", value),))):
            selected = service.read(scope).options.matches
            assert len(selected) == 1 and selected[0].snapshot.equipment_code == expected
            assert selected[0].snapshot.line == value
    assert {match.snapshot.equipment_code: match.snapshot.line for match in service.read(QueryScope(day)).options.matches} == {
        f"BE011-LINE-{index}": value for index, value in enumerate(values)
    }
