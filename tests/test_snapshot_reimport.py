"""BE-009 저장·부분 재가져오기를 실제 SQLite 트랜잭션으로 검증한다. MySQL 검증과 구분한다."""

from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import (
    Column, Date, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, MetaData,
    String, Table, UniqueConstraint, create_engine, event, func, select,
)
from sqlalchemy.exc import OperationalError

from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import DuplicateSaveError, ImportBlockedError
from setron.features.excel_import.service import ImportValidationService, build_save_request, edit_value
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.service import StorageService
from setron.features.storage.validation import TABLE_NAMES, StorageValidationError
from setron.infrastructure.database import Database, DatabaseSaveError
from setron.features.data_admin import WORKBOOK_KEY  # 11-10: 가져오기 원본에 파일 이름도 보관

from test_import_validation import draft, row


@pytest.fixture
def storage():
    """필수값·고유키·외래키를 적용한 임시 업무 DB. 회사 자료와 외부 서버를 사용하지 않는다."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    Table("import_batch", metadata,
          Column("batch_id", Integer, primary_key=True), Column("source_type", String, nullable=False),
          Column("raw_data", String))
    Table("equipment_group", metadata,
          Column("group_id", Integer, primary_key=True), Column("group_name", String(50), nullable=False),
          Column("category", String(10), nullable=False), Column("parent_group_id", Integer),
          Column("analysis_enabled", Integer, nullable=False, server_default="1"))
    Table("version_family", metadata,
          Column("family_id", Integer, primary_key=True),
          Column("group_id", Integer, ForeignKey("equipment_group.group_id"), nullable=False),
          Column("family_name", String(100), nullable=False), Column("ui_type", String(20)))
    Table("release_version", metadata,
          Column("release_version_id", Integer, primary_key=True),
          Column("family_id", Integer, ForeignKey("version_family.family_id"), nullable=False),
          Column("version_raw", LargeBinary(400), nullable=False), Column("version_order", Integer),
          Column("released_at", Date), Column("batch_id", Integer, ForeignKey("import_batch.batch_id")),
          Column("source_reference", String), Column("edited_fields", String, nullable=False),
          Column("item_mapping_status", String, nullable=False),
          UniqueConstraint("family_id", "version_raw"))
    Table("equipment", metadata,
          Column("equipment_id", Integer, primary_key=True),
          Column("equipment_code", LargeBinary(200), nullable=False, unique=True))
    Table("equipment_snapshot", metadata,
          Column("snapshot_id", Integer, primary_key=True),
          Column("equipment_id", Integer, ForeignKey("equipment.equipment_id"), nullable=False),
          Column("base_date", Date, nullable=False), Column("site", String(50), nullable=False),
          Column("line", String(50)), Column("model_name", String(255), nullable=False),
          Column("group_id", Integer, ForeignKey("equipment_group.group_id")),
          Column("production_area", String(20)), Column("structure_type", String(50)),
          Column("geometry_type", String(50)), Column("modification_type", String(100)),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False),
          Column("source_reference", String), UniqueConstraint("equipment_id", "base_date"))
    Table("equipment_version", metadata,
          Column("equipment_version_id", Integer, primary_key=True),
          Column("snapshot_id", Integer, ForeignKey("equipment_snapshot.snapshot_id"), nullable=False),
          Column("family_id", Integer, ForeignKey("version_family.family_id"), nullable=False),
          Column("version_raw", LargeBinary(400), nullable=False),
          Column("release_version_id", Integer, ForeignKey("release_version.release_version_id")),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False),
          Column("source_reference", String),
          UniqueConstraint("snapshot_id", "equipment_version_id"),
          UniqueConstraint("snapshot_id", "family_id", "version_raw"))
    Table("equipment_pm", metadata,
          Column("pm_id", Integer, primary_key=True),
          Column("snapshot_id", Integer, ForeignKey("equipment_snapshot.snapshot_id"), nullable=False),
          Column("equipment_version_id", Integer), Column("pm_name", LargeBinary(200), nullable=False),
          Column("group_id", Integer, ForeignKey("equipment_group.group_id"), nullable=False),
          Column("model_name_raw", String(255)),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False),
          Column("source_reference", String), UniqueConstraint("snapshot_id", "pm_name"),
          ForeignKeyConstraint(["snapshot_id", "equipment_version_id"],
                               ["equipment_version.snapshot_id", "equipment_version.equipment_version_id"]))
    Table("app_setting", metadata, Column("setting_key", LargeBinary(400), primary_key=True),
          Column("setting_value", String, nullable=False))
    for name in TABLE_NAMES:
        if name not in metadata.tables:
            Table(name, metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    service = StorageService(Database(engine))
    # SQLite BLOB 반영은 길이를 잃으므로 업무 검증에는 위 정의의 MySQL VARBINARY 길이를 사용한다.
    for name in ("equipment", "equipment_version", "equipment_pm", "release_version"):
        for column in service.repository.table(name).c:
            if isinstance(column.type, LargeBinary):
                column.type.length = metadata.tables[name].c[column.name].type.length
    with service.transaction() as tx:
        tx.insert("equipment_group", {"group_id": 1, "group_name": "M-O2", "category": "OXIDE"})
        tx.insert("equipment_group", {"group_id": 2, "group_name": "M-O3", "category": "OXIDE"})
        tx.insert("version_family", {"family_id": 1, "group_id": 1, "family_name": "O2", "ui_type": None})
        tx.insert("version_family", {"family_id": 2, "group_id": 2, "family_name": "O3", "ui_type": None})
        tx.insert("version_family", {"family_id": 3, "group_id": 1, "family_name": "O2 보조", "ui_type": None})
        for number in (1, 2):
            tx.insert("release_version", {"release_version_id": number, "family_id": 1,
                                         "version_raw": f"v{number}", "version_order": number})
    try:
        yield service
    finally:
        engine.dispose()


def save(storage, *rows, request_id="save", sheet="현황"):
    source = draft(*rows, sheet=sheet)
    request, = ImportValidationService(storage).prepare((source,), request_id)
    return DatabaseImportSaver(storage).save(request)


def snapshot(storage, code="E-1", base="2026-11-02"):
    return EquipmentService(storage).get_snapshot(code, date.fromisoformat(base))


def pm_versions(value):
    return {pm.pm_name: (software.family_id, software.version_raw, pm.pm_id)
            for software in value.software for pm in software.pms}


def count(storage, table):
    with storage.transaction() as tx:
        return tx.session.scalar(select(func.count()).select_from(tx.repository.table(table)))


def original_value(storage, record, field):
    """기록 자신의 입력 작업·셀 위치로 원본을 조회한다. 공유 SW의 대표 출처로 대신하지 않는다."""
    location = record.source_reference[field]
    with storage.transaction() as tx:
        raw = tx.get("import_batch", {"batch_id": record.batch_id})["raw_data"]
    return raw[location["sheet"]][location["cells"][0]]


def test_initial_save_registers_equipment_and_keeps_raw_values_separate_from_edits(storage):
    source = draft(row(version_raw="v1"), sheet="원본 현황")
    edited = edit_value(source, 1, "version_raw", "v2")
    request, = ImportValidationService(storage).prepare((edited,), "new")
    receipt = DatabaseImportSaver(storage).save(request)
    stored = snapshot(storage)
    assert count(storage, "equipment") == 1
    assert stored.software[0].version_raw == "v2"
    assert stored.software[0].release_version_id == 2
    with storage.transaction() as tx:
        raw = tx.get("import_batch", {"batch_id": receipt.batch_id})["raw_data"]
    assert raw == {"원본 현황": source.sheet.cells, WORKBOOK_KEY: source.workbook_name}
    assert raw["원본 현황"][source.row(1).sources["version_raw"]] == "v1"
    assert "합성.xlsx" not in str(raw["원본 현황"])  # 11-10: 파일 이름은 시트 셀과 섞지 않고 WORKBOOK_KEY에만 둔다


def test_same_values_reuse_snapshot_software_and_fixed_pm_ids(storage):
    first_receipt = save(storage, row())
    before = snapshot(storage)
    latest_receipt = save(storage, row(), request_id="again", sheet="다시 읽은 현황")
    after = snapshot(storage)
    assert after.equipment_id == before.equipment_id and after.snapshot_id == before.snapshot_id
    assert after.software[0].equipment_version_id == before.software[0].equipment_version_id
    assert pm_versions(after) == pm_versions(before)
    assert count(storage, "equipment") == count(storage, "equipment_snapshot") == count(storage, "equipment_version") == 1
    assert after.batch_id == after.software[0].batch_id == after.software[0].pms[0].batch_id == latest_receipt.batch_id
    assert after.batch_id != first_receipt.batch_id
    assert after.source_reference["equipment_code"]["sheet"] == "다시 읽은 현황"
    assert after.software[0].source_reference["version_raw"]["sheet"] == "다시 읽은 현황"
    assert after.software[0].pms[0].source_reference["version_raw"]["sheet"] == "다시 읽은 현황"
    assert original_value(storage, after.software[0].pms[0], "version_raw") == "v1"
    assert original_value(storage, before.software[0].pms[0], "version_raw") == "v1"


def test_unchanged_partial_pm_uses_latest_own_source_and_preserves_unimported_shared_source(storage):
    first_receipt = save(storage, row(), row(2, pm_name="PM2"), sheet="이전 원본")
    before = snapshot(storage)
    previous_sw, = before.software
    previous_pms = {pm.pm_name: pm for pm in previous_sw.pms}
    latest_receipt = save(storage, row(), request_id="partial-same", sheet="이번 원본")
    after = snapshot(storage)
    shared_sw, = after.software
    pms = {pm.pm_name: pm for pm in shared_sw.pms}
    assert shared_sw.equipment_version_id == previous_sw.equipment_version_id
    assert shared_sw.batch_id == first_receipt.batch_id
    assert shared_sw.source_reference == previous_sw.source_reference
    assert pms["PM2"] == previous_pms["PM2"]
    assert pms["PM1"].pm_id == previous_pms["PM1"].pm_id
    assert pms["PM1"].batch_id == latest_receipt.batch_id
    assert pms["PM1"].source_reference["version_raw"]["sheet"] == "이번 원본"
    assert pms["PM2"].source_reference["version_raw"]["sheet"] == "이전 원본"
    assert original_value(storage, pms["PM1"], "version_raw") == "v1"
    assert original_value(storage, pms["PM2"], "version_raw") == "v1"


def test_changed_pm_keeps_latest_raw_source_while_other_pm_retains_its_original_batch(storage):
    first_receipt = save(storage, row(), row(2, pm_name="PM2"), sheet="변경 전")
    before = snapshot(storage)
    old_sw, = before.software
    old_pm2 = next(pm for pm in old_sw.pms if pm.pm_name == "PM2")
    source = draft(row(version_raw="v2"), sheet="변경 후")
    edited = edit_value(source, 1, "version_raw", "v3")
    request, = ImportValidationService(storage).prepare((edited,), "changed-own-source")
    latest_receipt = DatabaseImportSaver(storage).save(request)
    after = snapshot(storage)
    changed_sw = next(sw for sw in after.software if sw.version_raw == "v3")
    retained_sw = next(sw for sw in after.software if sw.version_raw == "v1")
    pm1, = changed_sw.pms
    pm2, = retained_sw.pms
    assert changed_sw.batch_id == pm1.batch_id == latest_receipt.batch_id
    assert pm1.source_reference["version_raw"]["sheet"] == "변경 후"
    assert original_value(storage, pm1, "version_raw") == original_value(storage, changed_sw, "version_raw") == "v2"
    assert retained_sw.equipment_version_id == old_sw.equipment_version_id
    assert retained_sw.batch_id == first_receipt.batch_id and retained_sw.source_reference == old_sw.source_reference
    assert pm2 == old_pm2
    assert original_value(storage, pm2, "version_raw") == "v1"


def test_one_shared_pm_changes_without_changing_other_pm_family_day_or_equipment(storage):
    save(storage, row(), row(2, pm_name="PM2"),
         row(3, pm_name="PM3", model_name="M-O3", family_name="O3", version_raw="O3-v1"))
    save(storage, row(base_date="2026-11-01", version_raw="v0"), request_id="old-day")
    save(storage, row(equipment_code="OTHER", version_raw="v0"), request_id="other-equipment")
    before = snapshot(storage)
    old_day, other = snapshot(storage, base="2026-11-01"), snapshot(storage, "OTHER")
    save(storage, row(version_raw="v2"), request_id="pm1-change")
    after = snapshot(storage)
    assert pm_versions(after)["PM1"] == (1, "v2", pm_versions(before)["PM1"][2])
    assert {name: value for name, value in pm_versions(after).items() if name != "PM1"} == {
        name: value for name, value in pm_versions(before).items() if name != "PM1"}
    assert snapshot(storage, base="2026-11-01") == old_day
    assert snapshot(storage, "OTHER") == other
    assert after.snapshot_id == before.snapshot_id


def test_pm_versions_can_split_and_merge_without_leaving_a_phantom_software_record(storage):
    save(storage, row(), row(2, pm_name="PM2"))
    first = snapshot(storage)
    assert len(first.software) == 1
    save(storage, row(version_raw="v2"), request_id="split")
    assert len(snapshot(storage).software) == 2
    save(storage, row(version_raw="v1"), request_id="merge")
    final = snapshot(storage)
    assert len(final.software) == count(storage, "equipment_version") == 1
    assert pm_versions(final) == pm_versions(first)
    assert final.software[0].equipment_version_id == first.software[0].equipment_version_id


def test_new_fixed_pm_is_added_and_other_positions_are_preserved(storage):
    save(storage, row())
    before = snapshot(storage)
    save(storage, row(pm_name="PM2", version_raw="v2"), request_id="new-pm")
    after = snapshot(storage)
    assert pm_versions(after)["PM1"] == pm_versions(before)["PM1"]
    assert pm_versions(after)["PM2"][:2] == (1, "v2")
    assert len(pm_versions(after)) == 2


def test_fixed_pm_names_are_exact_and_not_case_folded(storage):
    save(storage, row())
    save(storage, row(pm_name="pm1", version_raw="v2"), request_id="lower-case-pm")
    assert {name: value[:2] for name, value in pm_versions(snapshot(storage)).items()} == {
        "PM1": (1, "v1"), "pm1": (1, "v2")}


def test_missing_date_is_not_filled_with_another_day_or_current_information(storage):
    save(storage, row())
    assert snapshot(storage, base="2026-11-01") is None
    save(storage, row(base_date="2026-11-03", site="화성", line="새 라인", version_raw="v2"), request_id="next-day")
    assert snapshot(storage).base_date == date(2026, 11, 2) and snapshot(storage).site == "화성"
    assert snapshot(storage).line is None
    assert snapshot(storage, base="2026-11-03").site == "화성"
    assert snapshot(storage, base="2026-11-03").line == "새 라인"
    assert snapshot(storage, base="2026-11-01") is None


@pytest.mark.parametrize("base", ["2026-11-02", "2026-11-03"])
def test_equipment_site_is_fixed_even_when_importing_another_date(storage, base):
    save(storage, row())
    before = snapshot(storage)
    source = draft(row(base_date=base, site="평택", version_raw="v2"))
    with pytest.raises(ImportBlockedError):
        ImportValidationService(storage).prepare((source,), "changed-site")
    assert snapshot(storage) == before
    assert count(storage, "equipment_snapshot") == 1


def test_general_equipment_updates_only_requested_family(storage):
    save(storage, row(pm_name=None), row(2, pm_name=None, family_name="O2 보조", version_raw="aux1"))
    before = snapshot(storage)
    save(storage, row(pm_name=None, version_raw="v2"), request_id="one-family")
    after = snapshot(storage)
    assert {sw.family_id: sw.version_raw for sw in after.software} == {1: "v2", 3: "aux1"}
    assert next(sw for sw in after.software if sw.family_id == 3) == next(sw for sw in before.software if sw.family_id == 3)
    assert all(not sw.pms for sw in after.software)


def test_unreviewed_existing_snapshot_is_not_overwritten(storage):
    save(storage, row())
    before = snapshot(storage)
    request = build_save_request(draft(row(version_raw="v2")), "unreviewed")
    with pytest.raises(StorageValidationError):
        DatabaseImportSaver(storage).save(request)
    assert snapshot(storage) == before


def test_changed_database_value_requires_review_again(storage):
    save(storage, row())
    pending, = ImportValidationService(storage).prepare((draft(row(version_raw="v2")),), "pending")
    save(storage, row(version_raw="v3"), request_id="other-change")
    latest = snapshot(storage)
    with pytest.raises(StorageValidationError):
        DatabaseImportSaver(storage).save(pending)
    assert snapshot(storage) == latest


def test_new_snapshot_created_after_review_does_not_get_silently_overwritten(storage):
    pending, = ImportValidationService(storage).prepare((draft(row(version_raw="v2")),), "pending-new")
    save(storage, row(version_raw="v3"), request_id="competing-new")
    before = snapshot(storage)
    with pytest.raises(StorageValidationError):
        DatabaseImportSaver(storage).save(pending)
    assert snapshot(storage) == before


def test_bad_later_sheet_rolls_back_prior_updates_registration_and_original_batch(storage):
    save(storage, row())
    before, initial_batches = snapshot(storage), count(storage, "import_batch")
    drafts = (draft(row(version_raw="v2"), sheet="갱신"),
              draft(row(equipment_code="NEW-ROLLBACK"), sheet="신규"))
    requests = ImportValidationService(storage).prepare(drafts, "atomic")
    engine = storage.database.engine

    def fail_new_registration(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO EQUIPMENT ") and b"NEW-ROLLBACK" in parameters:
            raise OperationalError(statement, parameters, RuntimeError("테스트 저장 실패"))

    event.listen(engine, "before_cursor_execute", fail_new_registration)
    try:
        with pytest.raises(DatabaseSaveError):
            DatabaseImportSaver(storage).save_many(requests)
    finally:
        event.remove(engine, "before_cursor_execute", fail_new_registration)
    assert snapshot(storage) == before
    assert snapshot(storage, "NEW-ROLLBACK") is None
    assert count(storage, "import_batch") == initial_batches
    assert count(storage, "equipment") == 1


def test_multiple_reviewed_sheets_for_one_snapshot_keep_both_pm_updates(storage):
    save(storage, row(), row(2, pm_name="PM2"))
    sources = (draft(row(version_raw="v2"), sheet="첫 PM"),
               draft(row(pm_name="PM2", version_raw="v3"), sheet="둘째 PM"))
    requests = ImportValidationService(storage).prepare(sources, "both-pm")
    receipts = DatabaseImportSaver(storage).save_many(requests)
    assert len(receipts) == 2
    assert {pm: values[:2] for pm, values in pm_versions(snapshot(storage)).items()} == {
        "PM1": (1, "v2"), "PM2": (1, "v3")}


@pytest.mark.parametrize("changes", [{"version_raw": None}, {"equipment_code": None}, {"site": None}])
def test_required_value_errors_hold_the_whole_reviewed_batch(storage, changes):
    sources = (draft(row(equipment_code="GOOD"), sheet="정상"), draft(row(**changes), sheet="오류"))
    with pytest.raises(ImportBlockedError):
        ImportValidationService(storage).prepare(sources, "invalid")
    assert count(storage, "equipment") == count(storage, "import_batch") == 0


def test_cross_sheet_same_pm_conflict_holds_the_whole_batch(storage):
    sources = (draft(row(), sheet="시트1"), draft(row(version_raw="v2"), sheet="시트2"))
    with pytest.raises(ImportBlockedError):
        ImportValidationService(storage).prepare(sources, "pm-conflict")
    assert count(storage, "equipment") == count(storage, "import_batch") == 0


def test_storage_rechecks_required_values_instead_of_trusting_prepared_request(storage):
    source = draft(row(equipment_code="GOOD"), row(2, equipment_code="BAD"))
    prepared, = ImportValidationService(storage).prepare((source,), "changed-request")
    good, bad = prepared.rows
    changed = replace(prepared, rows=(good, replace(bad, values={**bad.values, "version_raw": None})))
    with pytest.raises(StorageValidationError):
        DatabaseImportSaver(storage).save(changed)
    assert count(storage, "equipment") == count(storage, "import_batch") == 0


def test_storage_rechecks_cross_sheet_conflicts_before_either_sheet_is_saved(storage):
    sources = (draft(row(), sheet="시트1"), draft(row(pm_name="PM2"), sheet="시트2"))
    first, second = ImportValidationService(storage).prepare(sources, "changed-pm-request")
    value, = second.rows
    changed = replace(second, rows=(replace(value, values={**value.values, "pm_name": "PM1", "version_raw": "v2"}),))
    with pytest.raises(StorageValidationError):
        DatabaseImportSaver(storage).save_many((first, changed))
    assert count(storage, "equipment") == count(storage, "import_batch") == 0


def test_successful_save_request_cannot_be_applied_twice(storage):
    request, = ImportValidationService(storage).prepare((draft(row()),), "once")
    saver = DatabaseImportSaver(storage)
    saver.save(request)
    with pytest.raises(DuplicateSaveError):
        saver.save(request)
    assert count(storage, "import_batch") == 1


def test_unregistered_hotfix_is_preserved_without_fabricating_a_release(storage):
    save(storage, row(version_raw="v1_미등록_n4(H)"))
    value = snapshot(storage).software[0]
    assert value.version_raw == "v1_미등록_n4(H)" and value.release_version_id is None
