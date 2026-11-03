"""SQLite에서 실제 SQL 조회와 BE-008 전체 묶음 검증을 확인한다. MySQL 검증과 구분한다."""

from datetime import date

import pytest
from sqlalchemy import Column, Date, Integer, LargeBinary, MetaData, String, Table, create_engine, event, select

from setron.features.excel_import.service import ImportValidationService, has_errors, validate
from setron.features.excel_import.reference import resolve_family
from setron.features.excel_import.schemas import ExtractKind, ImportBlockedError
from setron.features.storage.service import StorageService
from setron.features.storage.validation import TABLE_NAMES
from setron.features.version.schemas import CompareReason
from setron.infrastructure.database import Database, DatabaseConnectionError

from test_import_validation import draft, row as review_row


def row(*args, **changes):
    """DB 기존 호기의 고정 사업장과 같은 검토 행을 만들고 시험할 변경만 지정한다."""
    changes.setdefault("site", "평택")
    return review_row(*args, **changes)


@pytest.fixture
def database():
    """실제 업무 조회 컬럼을 가진 임시 SQLite DB와 읽기 SQL 기록을 준비한다.

    return
    - 공통 StorageService, 엔진, 테이블 사전, 실행 SQL 목록. 테스트 종료 시 연결 정리.
    """
    engine = create_engine("sqlite://")
    metadata = MetaData()
    definitions = {
        "equipment_group": (("group_id", Integer), ("group_name", String), ("category", String), ("parent_group_id", Integer)),
        "version_family": (("family_id", Integer), ("group_id", Integer), ("family_name", String), ("ui_type", String)),
        "release_version": (("release_version_id", Integer), ("family_id", Integer), ("version_raw", LargeBinary),
                            ("version_order", Integer), ("released_at", Date)),
        "equipment": (("equipment_id", Integer), ("equipment_code", LargeBinary)),
        "equipment_snapshot": (("snapshot_id", Integer), ("equipment_id", Integer), ("base_date", Date),
            ("site", String), ("line", String), ("model_name", String), ("batch_id", Integer), ("group_id", Integer),
            ("production_area", String), ("structure_type", String), ("geometry_type", String),
            ("modification_type", String), ("source_reference", String)),
        "equipment_version": (("equipment_version_id", Integer), ("snapshot_id", Integer), ("family_id", Integer),
            ("version_raw", LargeBinary), ("release_version_id", Integer), ("batch_id", Integer), ("source_reference", String)),
        "equipment_pm": (("pm_id", Integer), ("snapshot_id", Integer), ("equipment_version_id", Integer),
            ("pm_name", LargeBinary), ("group_id", Integer), ("model_name_raw", String), ("batch_id", Integer), ("source_reference", String)),
        "release_version_item": (("release_version_id", Integer), ("item_id", Integer)),
        "item": (("item_id", Integer), ("item_no", Integer), ("content", String)),
    }
    for name in TABLE_NAMES:
        columns = definitions.get(name, (("id", Integer),))
        Table(name, metadata, *(Column(field, kind, primary_key=i == 0) for i, (field, kind) in enumerate(columns)))
    metadata.create_all(engine)
    tables = metadata.tables
    with engine.begin() as connection:
        connection.execute(tables["equipment_group"].insert(), [
            dict(group_id=1, group_name="M-O2", category="OXIDE"), dict(group_id=2, group_name="M-O3", category="OXIDE"),
        ])
        connection.execute(tables["version_family"].insert(), [
            dict(family_id=1, group_id=1, family_name="O2", ui_type=None),
            dict(family_id=2, group_id=2, family_name="O3", ui_type=None),
        ])
        connection.execute(tables["release_version"].insert(), [
            dict(release_version_id=1, family_id=1, version_raw=b"v1", version_order=1, released_at=None),
            dict(release_version_id=2, family_id=1, version_raw=b"v2", version_order=2, released_at=None),
        ])
        connection.execute(tables["equipment"].insert(), dict(equipment_id=1, equipment_code=b"E-1"))
        connection.execute(tables["equipment_snapshot"].insert(), dict(
            snapshot_id=1, equipment_id=1, base_date=date(2026, 11, 2), site="평택", line="기존 라인", model_name="M-O2", group_id=1, batch_id=1,
        ))
        connection.execute(tables["equipment_version"].insert(), dict(
            equipment_version_id=1, snapshot_id=1, family_id=1, version_raw=b"v2", release_version_id=2, batch_id=1,
        ))
        connection.execute(tables["equipment_pm"].insert(), dict(
            pm_id=1, snapshot_id=1, equipment_version_id=1, pm_name=b"PM1", group_id=1, model_name_raw="M-O2", batch_id=1,
        ))
    storage = StorageService(Database(engine))
    commands = []

    def record(connection, cursor, statement, parameters, context, executemany):
        """검증 실행 중 SQL 종류를 기록해 쓰기가 없음을 확인한다.

        param
        - connection, cursor, statement, parameters, context, executemany: SQLAlchemy 실행 이벤트 인자.
        """
        commands.append(statement.strip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield storage, engine, tables, commands
    finally:
        event.remove(engine, "before_cursor_execute", record)
        engine.dispose()


def test_db_values_are_compared_read_only_and_missing_dates_are_not_filled(database):
    """실제 SQL로 전체 사업장 동일 호기와 정확한 날짜만 조회하고 기존값을 변경하지 않는다.

    param
    - database: 임시 SQLite 공통 저장 서비스와 SQL 기록.
    """
    storage, engine, tables, commands = database
    service = ImportValidationService(storage)
    issues = service.validate((draft(row(line="새 라인")),), targets={(1, 1): 2})
    assert {(i.field, i.previous_value) for i in issues if i.code == "STORED_VALUE_CHANGED"} == {("line", "기존 라인"), ("version_raw", "v2")}
    assert next(i.comparison for i in issues if i.comparison).label == "N-1"
    other_day = service.validate((draft(row(base_date="2026-11-01")),), targets={(1, 1): 2})
    assert not any(i.code == "STORED_VALUE_CHANGED" for i in other_day)
    assert not any(i.code == "NEW_EQUIPMENT" for i in other_day)
    requests = service.prepare((draft(row(line="새 라인")),), "r", targets={(1, 1): 2}, aliases={2: {"확정 별칭": "다른 계열 원문"}})
    assert len(requests) == 1 and requests[0].request_id == "r:1"
    assert all(command == "SELECT" for command in commands)
    with engine.connect() as connection:
        assert connection.execute(select(tables["equipment_version"].c.version_raw)).scalar_one() == b"v2"


def test_prepare_holds_all_sheets_for_cross_sheet_conflict(database):
    """정상 시트만 먼저 요청으로 반환하지 않고 여러 시트 사이 충돌도 차단한다.

    param
    - database: 임시 SQLite 공통 저장 서비스와 SQL 기록.
    """
    storage, _, _, commands = database
    service = ImportValidationService(storage)
    drafts = (draft(row(), sheet="Memory"), draft(row(version_raw="v2"), sheet="Foundry"))
    with pytest.raises(ImportBlockedError):
        service.prepare(drafts, "r")
    assert all(command == "SELECT" for command in commands)


def test_next_validation_reads_changed_release_order_and_unregistered_hotfix_is_allowed(database):
    """재검증 때 최신 순서를 읽고 자유 핫픽스의 저장 가능과 판단 불가를 함께 제공한다.

    param
    - database: 임시 SQLite 공통 저장 서비스.
    """
    storage, engine, tables, _ = database
    service = ImportValidationService(storage)
    with engine.begin() as connection:
        connection.execute(tables["release_version"].update().where(tables["release_version"].c.release_version_id == 1)
                           .values(version_order=None))
    issues = service.validate((draft(row()),), targets={(1, 1): 2})
    assert next(i.comparison for i in issues if i.comparison).reason is CompareReason.ORDER_UNKNOWN
    requests = service.prepare((draft(row(version_raw="v1_정해지지 않은 핫픽스")),), "hotfix", targets={(1, 1): 2})
    assert requests[0].rows[0].values["version_raw"] == "v1_정해지지 않은 핫픽스"


def test_db_read_failure_is_not_reported_as_successful_validation(database, monkeypatch):
    """DB 조회 실패를 기준 자료 없음이나 검증 성공으로 숨기지 않는다.

    param
    - database: 임시 SQLite 공통 저장 서비스.
    - monkeypatch: 조회 실패를 주입하는 pytest 도구.
    """
    storage, _, _, _ = database

    def unavailable():
        """테스트용 DB 접속 실패를 발생시킨다.

        raises
        - DatabaseConnectionError: 조회할 DB에 접속할 수 없는 경우.
        """
        raise DatabaseConnectionError("테스트 접속 실패")

    monkeypatch.setattr(storage, "transaction", unavailable)
    with pytest.raises(DatabaseConnectionError):
        ImportValidationService(storage).prepare((draft(row()),), "r")


def test_unregistered_model_blocks_db_validation_and_whole_prepare_batch(database):
    """계열이 등록됐어도 DB에 없는 입력 모델을 차단하고 정상 시트만 먼저 반환하지 않는다.

    param
    - database: 등록 모델과 계열을 조회할 임시 SQLite 공통 저장 서비스.
    """
    storage, _, _, commands = database
    service = ImportValidationService(storage)
    invalid = draft(row(model_name="DB에 없는 모델", equipment_code="NEW-UNKNOWN"), sheet="잘못된 모델")
    issues = service.validate((invalid,))
    model_errors = [issue for issue in issues if issue.code == "MODEL_NOT_REGISTERED"]
    assert len(model_errors) == 1 and model_errors[0].field == "model_name" and model_errors[0].blocks_save
    assert model_errors[0].sheet_name == "잘못된 모델" and model_errors[0].address == "E1"
    with pytest.raises(ImportBlockedError):
        service.prepare((draft(row()), invalid), "all-or-nothing")
    assert commands and all(command == "SELECT" for command in commands)


@pytest.mark.parametrize("category,ambiguous", [("KCTC", False), ("OXIDE", True)])
def test_model_matching_is_unique_within_its_category(database, category, ambiguous):
    """모델명이 같아도 다른 분류는 구분하고 같은 분류의 중복 등록은 모호한 대응으로 차단한다.

    param
    - database: 모델 등록을 추가할 임시 SQLite 공통 저장 서비스.
    - category: 같은 이름의 모델을 추가할 분류.
    - ambiguous: 입력 분류 안에서 모델 대응이 여러 개가 되는지 여부.
    """
    storage, engine, tables, _ = database
    with engine.begin() as connection:
        connection.execute(tables["equipment_group"].insert(), dict(group_id=3, group_name="M-O2", category=category))
    service = ImportValidationService(storage)
    issues = service.validate((draft(row()),))
    assert any(issue.code == "MODEL_AMBIGUOUS" for issue in issues) is ambiguous
    if ambiguous:
        with pytest.raises(ImportBlockedError):
            service.prepare((draft(row()),), "ambiguous")
    else:
        assert not has_errors(issues) and len(service.prepare((draft(row()),), "category-specific")) == 1


def test_registered_model_in_another_category_does_not_satisfy_input_model(database):
    """다른 분류에만 등록된 모델명을 현재 분류의 정상 모델로 연결하지 않는다.

    param
    - database: 분류별 모델 등록을 조회할 임시 SQLite 공통 저장 서비스.
    """
    storage, engine, tables, _ = database
    with engine.begin() as connection:
        connection.execute(tables["equipment_group"].insert(), dict(group_id=3, group_name="등록된 별도 모델", category="POLY"))
    source = draft(row(model_name="등록된 별도 모델", group_name="OXIDE"))
    service = ImportValidationService(storage)
    assert any(issue.code == "MODEL_NOT_REGISTERED" for issue in service.validate((source,)))
    with pytest.raises(ImportBlockedError):
        service.prepare((source,), "wrong-category")


def test_confirmed_alias_and_derived_model_keep_shared_family_and_independent_targets(database):
    """M5 별칭과 개조 모델의 등록 여부를 확인하면서 공유 계열·독립 N·신규 호기·미등록 핫픽스를 유지한다.

    param
    - database: 원형·개조형 모델을 추가할 임시 SQLite 공통 저장 서비스.
    """
    storage, engine, tables, _ = database
    with engine.begin() as connection:
        connection.execute(tables["equipment_group"].insert(), [
            dict(group_id=10, group_name="M-O5", category="OXIDE", parent_group_id=None),
            dict(group_id=11, group_name="M-O5 Prime", category="OXIDE", parent_group_id=10),
        ])
        connection.execute(tables["version_family"].insert(), dict(family_id=10, group_id=10, family_name="O5", ui_type=None))
        connection.execute(tables["release_version"].insert(), [
            dict(release_version_id=10, family_id=10, version_raw=b"v1", version_order=1, released_at=None),
            dict(release_version_id=11, family_id=10, version_raw=b"v2", version_order=2, released_at=None),
        ])
    service = ImportValidationService(storage)
    base = draft(row(model_name="M5", family_name="O5", equipment_code="NEW-BASE"), sheet="원형")
    prime = draft(row(model_name="M-O5 Prime", family_name="O5", equipment_code="NEW-PRIME"), sheet="개조형")
    targets = {(10, 10): 10, (11, 10): 11}
    issues = service.validate((base, prime), targets=targets)
    assert not has_errors(issues)
    assert {issue.sheet_name: issue.comparison.label for issue in issues if issue.comparison} == {"원형": "N 이상", "개조형": "N-1"}
    assert {issue.sheet_name for issue in issues if issue.code == "NEW_EQUIPMENT"} == {"원형", "개조형"}
    requests = service.prepare((base, prime), "shared-family", targets=targets)
    assert [request.rows[0].values["model_name"] for request in requests] == ["M-O5", "M-O5 Prime"]
    assert [request.rows[0].values["family_name"] for request in requests] == ["O5", "O5"]
    hotfix = draft(row(model_name="M-O5 Prime", family_name="O5", equipment_code="NEW-HOTFIX", version_raw="v1_자유 접미사"))
    assert not has_errors(service.validate((hotfix,), targets=targets))
    assert service.prepare((hotfix,), "hotfix", targets=targets)[0].rows[0].values["version_raw"] == "v1_자유 접미사"
    unconfirmed = draft(row(model_name="M5 Prime", family_name="O5"))
    assert any(issue.code == "MODEL_NOT_REGISTERED" for issue in service.validate((unconfirmed,)))
    with engine.begin() as connection:
        connection.execute(tables["equipment_group"].update().where(tables["equipment_group"].c.group_id == 11)
                           .values(parent_group_id=None))
    assert any(issue.code == "FAMILY_UNRESOLVED" for issue in service.validate((prime,)))


def test_empty_loaded_model_catalog_is_distinct_from_unconnected_catalog(database):
    """DB에서 조회한 모델이 0개이면 미연결로 생략하지 않고 등록되지 않은 모델을 차단한다.

    param
    - database: 모델·계열을 비울 임시 SQLite 공통 저장 서비스.
    """
    storage, engine, tables, _ = database
    with engine.begin() as connection:
        for name in ("equipment_pm", "equipment_version", "equipment_snapshot", "equipment", "release_version", "version_family", "equipment_group"):
            connection.execute(tables[name].delete())
    service = ImportValidationService(storage)
    with storage.transaction() as tx:
        loaded = service.repository.load(tx, ())
    assert loaded.catalog_loaded and loaded.models == ()
    source = draft(row())
    assert any(issue.code == "MODEL_NOT_REGISTERED" for issue in service.validate((source,)))
    with pytest.raises(ImportBlockedError):
        service.prepare((source,), "empty-catalog")


@pytest.mark.parametrize("kind", [ExtractKind.EQUIPMENT_STATUS, ExtractKind.RELEASE_NOTE])
def test_registered_model_cannot_use_an_unrelated_models_family(database, kind):
    """M-O3 모델의 O2 계열 요청을 검증 단계에서 차단해 실제 저장의 소유 범위와 일치시킨다.

    param
    - database: M-O2→O2와 M-O3→O3가 각각 등록된 임시 SQLite DB.
    - kind: 설비 현황 또는 릴리즈 노트.
    """
    storage, _, _, commands = database
    source = draft(row(model_name="M-O3", family_name="O2", equipment_code="NEW-WRONG-FAMILY"), kind=kind)
    service = ImportValidationService(storage)
    issues = service.validate((source,))
    assert any(issue.code == "FAMILY_UNRESOLVED" for issue in issues)
    assert not any(issue.code == "MODEL_NOT_REGISTERED" for issue in issues)
    with pytest.raises(ImportBlockedError):
        service.prepare((source,), "unrelated-family")
    assert commands and all(command == "SELECT" for command in commands)


def test_equal_family_names_are_resolved_within_each_registered_models_owner_scope(database):
    """같은 분류·계열명도 관리 모델이 다르면 입력 모델의 계열만 선택한다.

    param
    - database: 같은 이름의 계열을 다른 모델에 추가할 임시 SQLite DB.
    """
    storage, engine, tables, _ = database
    with engine.begin() as connection:
        connection.execute(tables["version_family"].insert(), dict(family_id=3, group_id=2, family_name="O2", ui_type=None))
    sources = (
        draft(row(model_name="M-O2", family_name="O2"), sheet="모델 O2"),
        draft(row(model_name="M-O3", family_name="O2", equipment_code="NEW-O3-OWN"), sheet="모델 O3"),
    )
    service = ImportValidationService(storage)
    with storage.transaction() as tx:
        reference = service.repository.load(tx, sources)
    assert [resolve_family(source.row(1), source.kind, reference).releases.family_id for source in sources] == [1, 3]
    assert [resolve_family(source.row(1), source.kind, reference).owner_group_id for source in sources] == [1, 2]
    assert not has_errors(service.validate(sources))
    assert len(service.prepare(sources, "same-name-own-family")) == 2


@pytest.mark.parametrize("model", [None, "   "])
def test_db_connected_release_requires_model_without_changing_unconnected_draft_policy(database, model):
    """DB 저장할 릴리즈의 모델 공란을 차단하고 모델 자료가 미연결인 기존 초안 규칙은 유지한다.

    param
    - database: 등록 모델 자료를 조회할 임시 SQLite DB.
    - model: 누락 또는 공백뿐인 입력 모델명.
    """
    storage, _, _, _ = database
    source = draft(row(model_name=model, content="합성 Item"), kind=ExtractKind.RELEASE_NOTE)
    assert not has_errors(validate(source))
    service = ImportValidationService(storage)
    issues = service.validate((source,))
    assert any(issue.code == "MODEL_REQUIRED" and issue.field == "model_name" for issue in issues)
    with pytest.raises(ImportBlockedError):
        service.prepare((source,), "release-without-model")
