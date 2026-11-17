"""실제 MySQL 5.6.19에 스키마를 적용하고 앱 저장 경로의 호환성을 검증한다."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pymysql
import pytest
from pymysql.constants import CLIENT
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, event, inspect, text

from setron.features.storage.service import StorageService
from setron.features.storage.validation import TABLE_NAMES, StorageValidationError
from setron.infrastructure.database import (
    Database,
    DatabaseCommitUnknownError,
    DatabaseConfig,
    DatabaseConnectionError,
    DatabaseSaveError,
    create_mysql_engine,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"),
    reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


@pytest.fixture(scope="module")
def schema_database():
    """테스트 DB에 관리 스키마를 적용하고 이번 실행에서 만든 테이블만 정리한다.

    기존 관리 테이블이 있는 DB에서는 실행을 중단한다. SETRON_TEST_DB_ 접속
    설정을 사용하며, 같은 모듈의 테스트가 생성된 스키마를 공유한다.

    return
    - 스키마 검증 중 사용할 MySQL 연결 관리자.
    """
    config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
    database = Database.from_env(prefix="SETRON_TEST_DB_")
    schema_path = Path(__file__).resolve().parents[1] / "database" / "schema_mysql5619.sql"
    assert schema_path.is_file(), "검증할 스키마 파일이 없습니다."
    before = set(inspect(database.engine).get_table_names())
    assert not before.intersection(TABLE_NAMES), "기존 관리 테이블이 있는 DB에서는 스키마 검증을 실행하지 않습니다."
    try:
        connection = pymysql.connect(
            host=config.host, port=config.port, user=config.username, password=config.password,
            database=config.database, charset="utf8mb4", client_flag=CLIENT.MULTI_STATEMENTS,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(schema_path.read_text(encoding="utf-8"))
                while cursor.nextset():
                    pass
            connection.commit()
        finally:
            connection.close()
        yield database
    finally:
        # 이번 검증에서 만든 테이블만 외래키 관계에 맞는 순서로 삭제한다.
        created = set(inspect(database.engine).get_table_names()).intersection(TABLE_NAMES) - before
        if created:
            metadata = MetaData()
            metadata.reflect(bind=database.engine, only=created)
            metadata.drop_all(database.engine)
        database.dispose()


@pytest.fixture
def storage(schema_database):
    """검증용 스키마를 사용하는 공통 저장 서비스를 제공한다.

    param
    - schema_database: 관리 스키마가 적용된 테스트 DB 연결 관리자.

    return
    - 해당 DB에 연결된 공통 저장 서비스.
    """
    return StorageService(schema_database)


@pytest.fixture
def kill_test_connection(schema_database):
    """테스트가 확보한 MySQL 연결을 종료하는 함수를 제공한다.

    param
    - schema_database: 연결 종료 명령을 실행할 테스트 DB 연결 관리자.

    return
    - 연결 ID를 받아 별도 제어 연결에서 KILL CONNECTION을 실행하는 함수.
    """
    def kill(connection_id):
        """지정한 테스트 연결을 별도 DB 연결에서 종료한다.

        param
        - connection_id: 이번 테스트가 확보한 양의 정수 MySQL 연결 ID.
        """
        # 이번 테스트가 확보한 연결만 같은 계정의 별도 연결에서 종료한다.
        assert type(connection_id) is int and connection_id > 0
        with schema_database.engine.connect() as control:
            assert control.scalar(text("SELECT CONNECTION_ID()")) != connection_id
            control.execute(text("KILL CONNECTION :connection_id"), {"connection_id": connection_id})

    return kill


def graph(tx, suffix):
    """검증에 필요한 입력 작업·설비군·버전 계열을 같은 트랜잭션에 추가한다.

    param
    - tx: 테스트 자료를 추가할 공통 저장 트랜잭션.
    - suffix: 다른 테스트 자료와 이름을 구분할 문자열.

    return
    - 생성한 입력 작업 ID, 설비군 ID, 버전 계열 ID의 튜플.
    """
    batch = tx.insert("import_batch", {"source_type": "MANUAL"})
    group = tx.insert("equipment_group", {"group_name": "M-O2-" + suffix, "category": "OXIDE"})
    family = tx.insert("version_family", {"group_id": group["group_id"], "family_name": suffix})
    return batch["batch_id"], group["group_id"], family["family_id"]


def test_exact_server_version_and_all_tables(schema_database):
    """MySQL 5.6.19·테이블 이름 설정·전체 관리 테이블의 InnoDB 적용을 검증한다.

    param
    - schema_database: 프로젝트 스키마가 적용된 테스트 DB 연결 관리자.
    """
    with schema_database.engine.connect() as connection:
        assert connection.scalar(text("SELECT VERSION()")).split("-")[0] == "5.6.19"
        assert connection.scalar(text("SELECT @@lower_case_table_names")) == 1
        assert set(TABLE_NAMES).issubset(inspect(connection).get_table_names())
        engines = connection.execute(text(
            "SELECT DISTINCT ENGINE FROM information_schema.tables WHERE table_schema = DATABASE()"
        )).scalars().all()
        assert engines == ["InnoDB"]


def test_large_json_and_unicode_are_preserved(storage):
    """큰 JSON과 한글·이모지·특수문자 원문을 저장 후 그대로 읽을 수 있는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    """
    original = {"Memory😀": {"A1": "한글😀\"\\" * 12000, "A2": [1, True, None]}}
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": original})
        assert batch["raw_data"] == original
        saved_id = batch["batch_id"]
    with storage.transaction() as tx:
        assert tx.get("import_batch", {"batch_id": saved_id})["raw_data"] == original


def test_validation_failure_cancels_the_whole_batch(storage):
    """입력 검증 실패 시 같은 트랜잭션에서 먼저 추가한 설비도 취소되는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    """
    with pytest.raises(StorageValidationError):
        with storage.transaction() as tx:
            tx.insert("equipment", {"equipment_code": "ROLLBACK-CHECK"})
            tx.insert("equipment_group", {"group_name": "HPA", "category": "HPA", "analysis_enabled": True})
    with storage.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"), {"code": b"ROLLBACK-CHECK"}) == 0


def test_text_overflow_is_an_error_and_cancels_earlier_writes(storage):
    """TEXT 용량 초과가 DB 오류로 처리되고 앞선 저장도 취소되는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    """
    with pytest.raises(DatabaseSaveError):
        with storage.transaction() as tx:
            tx.insert("equipment", {"equipment_code": "TEXT-OVERFLOW"})
            batch = tx.insert("import_batch", {"source_type": "MANUAL"})
            # 한글 22,000자는 UTF-8로 66,000바이트여서 TEXT 한도인 65,535바이트를 넘는다.
            tx.insert("item", {"content": "가" * 22000, "batch_id": batch["batch_id"]})
    with storage.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"), {"code": b"TEXT-OVERFLOW"}) == 0


def test_exact_versions_and_pm_links_survive(storage):
    """버전의 대소문자·끝 공백과 PM별 버전 연결을 보존하고 원문 중복은 막는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    """
    with storage.transaction() as tx:
        batch_id, group_id, family_id = graph(tx, "exact")
        versions = [tx.insert("release_version", {"family_id": family_id, "version_raw": raw, "batch_id": batch_id}) for raw in ("v1", "V1", "v1 ")]
        assert len({row["release_version_id"] for row in versions}) == 3
        assert [row["version_raw"] for row in versions] == ["v1", "V1", "v1 "]
        equipment = tx.insert("equipment", {"equipment_code": "MIXED-PM"})
        snapshot = tx.insert("equipment_snapshot", {
            "equipment_id": equipment["equipment_id"], "base_date": date(2026, 10, 28),
            "site": "화성", "model_name": "M-O2", "batch_id": batch_id,
        })
        for number, version in enumerate(versions[:2], 1):
            applied = tx.insert("equipment_version", {
                "snapshot_id": snapshot["snapshot_id"], "family_id": family_id,
                "version_raw": version["version_raw"], "release_version_id": version["release_version_id"], "batch_id": batch_id,
            })
            pm = tx.insert("equipment_pm", {
                "snapshot_id": snapshot["snapshot_id"], "equipment_version_id": applied["equipment_version_id"],
                "pm_name": "PM" + str(number), "group_id": group_id, "batch_id": batch_id,
            })
            assert pm["equipment_version_id"] == applied["equipment_version_id"]
    with pytest.raises(DatabaseSaveError):
        with storage.transaction() as tx:
            tx.insert("release_version", {"family_id": family_id, "version_raw": "v1", "batch_id": batch_id})


@pytest.mark.parametrize("id_format", [int, str], ids=["integer-id", "string-id"])
def test_version_deletion_keeps_all_risk_references(storage, id_format):
    """버전 삭제 시 위험 근거를 보존하고 작업 취소 시 참조와 상태까지 복원되는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    - id_format: 삭제 API에 넘길 ID의 형식을 만드는 int 또는 str.
    """
    with storage.transaction() as tx:
        batch_id, _, family_id = graph(tx, "risk-version")
        version = tx.insert("release_version", {"family_id": family_id, "version_raw": "v2_가😀", "batch_id": batch_id})
        version_id = version["release_version_id"]
        risk = tx.insert("risk_rule", {
            "rule_type": "DIRECT_VERSION", "family_id": family_id,
            "release_version_id": version_id, "start_release_version_id": version_id, "end_release_version_id": version_id,
            "risk_reason": "기존 근거", "target_reference": {"manual": "유지"}, "mapping_status": "VERIFIED",
        })
        risk_id = risk["risk_rule_id"]
    with pytest.raises(ValueError):
        with storage.transaction() as tx:
            tx.delete_release_version(id_format(version_id))
            raise ValueError("전체 취소")
    with storage.transaction() as tx:
        restored = tx.get("risk_rule", {"risk_rule_id": risk_id})
        assert restored["target_reference"] == {"manual": "유지"}
        assert restored["mapping_status"] == "VERIFIED"
        for field in ("release_version_id", "start_release_version_id", "end_release_version_id"):
            assert restored[field] == version_id
        assert tx.get("release_version", {"release_version_id": version_id})["version_raw"] == "v2_가😀"
        tx.delete_release_version(id_format(version_id))
    with storage.transaction() as tx:
        saved = tx.get("risk_rule", {"risk_rule_id": risk_id})
        assert saved["mapping_status"] == "UNRESOLVED"
        assert saved["target_reference"]["manual"] == "유지"
        for name in ("direct_version", "start_version", "end_version"):
            assert saved["target_reference"][name]["version_raw"] == "v2_가😀"
        assert saved["release_version_id"] is None
        assert saved["start_release_version_id"] is None
        assert saved["end_release_version_id"] is None


@pytest.mark.parametrize("id_format", [int, str], ids=["integer-id", "string-id"])
def test_item_deletion_preserves_source_cells(storage, id_format):
    """내부 Item 삭제 시 원본 셀 근거를 위험 규칙에 남기고 미확인 상태로 바꾸는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    - id_format: 삭제 API에 넘길 ID의 형식을 만드는 int 또는 str.
    """
    with storage.transaction() as tx:
        batch_id, _, family_id = graph(tx, "risk-item")
        item = tx.insert("item", {"content": "수정항목😀", "batch_id": batch_id, "source_reference": {"content": {"sheet": "릴리즈", "cells": ["B2"]}}})
        risk = tx.insert("risk_rule", {
            "rule_type": "DEFECT_ITEM", "family_id": family_id, "item_id": item["item_id"],
            "risk_reason": "결함", "target_reference": {"manual": "유지"}, "mapping_status": "VERIFIED",
        })
    with storage.transaction() as tx:
        tx.delete_release_item(id_format(item["item_id"]))
    with storage.transaction() as tx:
        saved = tx.get("risk_rule", {"risk_rule_id": risk["risk_rule_id"]})
        assert saved["item_id"] is None
        assert saved["mapping_status"] == "UNRESOLVED"
        assert saved["target_reference"]["manual"] == "유지"
        assert saved["target_reference"]["item"] == {
            field: item[field]
            for field in ("item_id", "item_no", "release_note_id", "content", "batch_id", "source_reference")
        }
    with pytest.raises(LookupError):
        with storage.transaction() as tx:
            tx.get("item", {"item_id": item["item_id"]})


def test_storage_initialization_reports_mysql_authentication_failure(schema_database):
    """저장 서비스의 MySQL 인증 실패와 이후 정상 설정으로의 접속을 검증한다.

    param
    - schema_database: 정상 접속 확인에 사용할 테스트 DB 연결 관리자.
    """
    config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
    database = Database(create_mysql_engine(replace(config, password="invalid-" + uuid4().hex)))
    try:
        with pytest.raises(DatabaseConnectionError):
            StorageService(database)
    finally:
        database.dispose()

    # 접속 실패 뒤에도 정상 설정을 사용하는 서비스는 바로 시작할 수 있어야 한다.
    with StorageService(schema_database).transaction() as tx:
        assert tx.get("app_setting", {"setting_key": "version_age_thresholds"})["setting_value"] == {
            "warning": 1, "danger": 2, "unit": "YEAR",
        }


@pytest.mark.parametrize("invalid_type", ["suffix", "decimal-string", "float", "boolean"])
def test_deletion_rejects_ambiguous_ids_before_changing_any_rows(storage, invalid_type):
    """정수 ID로 명확히 해석할 수 없는 삭제 요청이 기존 자료를 바꾸지 않는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    - invalid_type: 잘못된 ID를 구성할 접미사·소수 문자열·실수·불리언 구분값.
    """
    with storage.transaction() as tx:
        batch_id, _, family_id = graph(tx, "invalid-id-" + invalid_type)
        item = tx.insert("item", {"content": "삭제하면 안 되는 항목", "batch_id": batch_id})
        risk = tx.insert("risk_rule", {
            "rule_type": "DEFECT_ITEM", "family_id": family_id, "item_id": item["item_id"],
            "risk_reason": "원본 유지", "target_reference": {"manual": "유지"}, "mapping_status": "VERIFIED",
        })
    item_id = item["item_id"]
    invalid = {
        "suffix": str(item_id) + "invalid",
        "decimal-string": str(item_id) + ".0",
        "float": float(item_id),
        "boolean": True,
    }[invalid_type]
    with pytest.raises(StorageValidationError):
        with storage.transaction() as tx:
            tx.delete_release_item(invalid)
    with storage.transaction() as tx:
        assert tx.get("item", {"item_id": item_id}) == item
        assert tx.get("risk_rule", {"risk_rule_id": risk["risk_rule_id"]}) == risk


@pytest.mark.parametrize("operation", ["insert", "update"])
def test_invalid_json_keys_cancel_the_whole_batch(storage, operation):
    """JSON 키 검증 실패 시 추가·수정 전체가 취소되고 입력과 기존 값이 보존되는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    - operation: 잘못된 JSON을 전달할 insert 또는 update 작업.
    """
    existing_key = "json_original_" + operation
    new_key = "json_rejected_" + operation
    code = "JSON-KEY-CANCEL-" + operation
    original = {"원본": {"cells": ["A1"], "value": "유지😀"}}
    invalid = {"rows": [{"1": "원래 값", 1: "덮어쓰면 안 되는 값"}]}
    with storage.transaction() as tx:
        tx.insert("app_setting", {"setting_key": existing_key, "setting_value": original})

    with pytest.raises(StorageValidationError, match="키.*문자열"):
        with storage.transaction() as tx:
            tx.insert("equipment", {"equipment_code": code})
            if operation == "insert":
                tx.insert("app_setting", {"setting_key": new_key, "setting_value": invalid})
            else:
                tx.update("app_setting", {"setting_key": existing_key}, {"setting_value": invalid})

    with storage.transaction() as tx:
        assert tx.get("app_setting", {"setting_key": existing_key})["setting_value"] == original
    with pytest.raises(LookupError):
        with storage.transaction() as tx:
            tx.get("app_setting", {"setting_key": new_key})
    with storage.database.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"),
            {"code": code.encode("utf-8")},
        ) == 0
    assert len(invalid["rows"][0]) == 2


def test_sql_delete_failure_restores_risk_evidence_and_earlier_writes(storage):
    """외래키로 삭제가 실패하면 위험 근거 변경과 앞선 저장도 취소되는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    """
    with storage.transaction() as tx:
        batch_id, _, family_id = graph(tx, "delete-restricted")
        item = tx.insert("item", {"content": "삭제 전 원본😀", "batch_id": batch_id})
        risk = tx.insert("risk_rule", {
            "rule_type": "DEFECT_ITEM", "family_id": family_id, "item_id": item["item_id"],
            "risk_reason": "보존 확인", "target_reference": {"manual": "원본 근거"}, "mapping_status": "VERIFIED",
        })

    # 근거 기록 다음의 DELETE에서 실제 외래키 오류가 나도록 테스트 전용 참조를 만든다.
    guard = Table(
        "delete_guard_" + uuid4().hex,
        storage.repository.metadata,
        Column("id", Integer, primary_key=True),
        Column("item_id", storage.repository.table("item").c.item_id.type, ForeignKey("item.item_id")),
        mysql_engine="InnoDB",
    )
    guard.create(storage.database.engine)
    try:
        with storage.database.transaction() as session:
            session.execute(guard.insert(), {"id": 1, "item_id": item["item_id"]})
        with pytest.raises(DatabaseSaveError):
            with storage.transaction() as tx:
                tx.insert("equipment", {"equipment_code": "DELETE-SQL-CANCEL"})
                tx.delete_release_item(str(item["item_id"]))

        with storage.transaction() as tx:
            saved = tx.get("risk_rule", {"risk_rule_id": risk["risk_rule_id"]})
            assert saved["target_reference"] == {"manual": "원본 근거"}
            assert saved["mapping_status"] == "VERIFIED"
            assert saved["item_id"] == item["item_id"]
            assert tx.get("item", {"item_id": item["item_id"]}) == item
        with storage.database.engine.connect() as connection:
            assert connection.scalar(
                text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"),
                {"code": b"DELETE-SQL-CANCEL"},
            ) == 0
    finally:
        guard.drop(storage.database.engine)


def test_concurrent_version_deletions_preserve_all_risk_evidence(schema_database):
    """같은 위험 규칙을 참조하는 버전들의 동시 삭제가 근거를 유실하지 않는지 검증한다.

    잠금 대기 초과·교착으로 취소된 작업은 취소 결과를 확인한 뒤 새 트랜잭션에서
    다시 실행한다. 최종 저장 결과가 불명확한 오류는 재시도 대상으로 다루지 않는다.

    param
    - schema_database: 동시 삭제 검증에 필요한 스키마를 준비하는 테스트 DB 연결 관리자.
    """
    # 잠금 대기 시간 설정이 다른 테스트에 남지 않도록 별도 연결 풀을 사용한다.
    database = Database.from_env(prefix="SETRON_TEST_DB_")
    try:
        storage = StorageService(database)
        with storage.transaction() as tx:
            batch_id, _, family_id = graph(tx, "concurrent-delete")
            versions = [
                tx.insert("release_version", {
                    "family_id": family_id, "version_raw": f"parallel_{number}_가😀", "batch_id": batch_id,
                })
                for number in (1, 2)
            ]
            first, second = [version["release_version_id"] for version in versions]
            risk = tx.insert("risk_rule", {
                "rule_type": "DIRECT_VERSION", "family_id": family_id,
                "release_version_id": first, "start_release_version_id": first, "end_release_version_id": second,
                "risk_reason": "동시 삭제 검증", "mapping_status": "VERIFIED", "target_reference": {"manual": "유지"},
            })

        markers = {version_id: f"CONCURRENT-DELETE-{version_id}" for version_id in (first, second)}
        barrier = Barrier(2, timeout=5)

        def delete_version(version_id):
            """다른 작업과 시점을 맞춰 버전을 삭제하고 확정된 결과를 반환한다.

            param
            - version_id: 이번 작업에서 삭제할 릴리즈 버전 ID.

            return
            - 버전 ID와 저장 성공 여부의 튜플. 잠금 대기 초과·교착으로 취소되면 False.

            raises
            - DatabaseCommitUnknownError: 최종 저장 단계에서 연결이 끊겨 결과를 확정할 수 없는 경우.
            - DatabaseSaveError: 잠금 대기 초과·교착 이외의 DB 저장 오류가 발생한 경우.
            """
            try:
                with storage.transaction() as tx:
                    tx.session.execute(text("SET SESSION innodb_lock_wait_timeout = 3"))
                    tx.insert("equipment", {"equipment_code": markers[version_id]})
                    storage.repository.get(tx.session, "release_version", {"release_version_id": version_id}, for_update=True)
                    # 두 작업이 각각 대상 행을 잠근 뒤 같은 위험 규칙을 갱신하도록 맞춘다.
                    barrier.wait()
                    tx.delete_release_version(str(version_id))
                return version_id, True
            except DatabaseCommitUnknownError:
                raise
            except DatabaseSaveError as error:
                mysql_error = getattr(error.__cause__, "orig", None)
                error_code = mysql_error.args[0] if getattr(mysql_error, "args", ()) else None
                if error_code not in {1205, 1213}:
                    raise
                return version_id, False

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(delete_version, version_id) for version_id in (first, second)]
            results = [future.result(timeout=15) for future in futures]

        reference_fields = {
            first: {"release_version_id": "direct_version", "start_release_version_id": "start_version"},
            second: {"end_release_version_id": "end_version"},
        }
        with storage.transaction() as tx:
            saved = tx.get("risk_rule", {"risk_rule_id": risk["risk_rule_id"]})
            assert saved["target_reference"]["manual"] == "유지"
            assert saved["mapping_status"] == ("UNRESOLVED" if any(committed for _, committed in results) else "VERIFIED")
            for version_id, committed in results:
                for field, label in reference_fields[version_id].items():
                    if committed:
                        assert saved[field] is None
                        assert saved["target_reference"][label]["release_version_id"] == version_id
                    else:
                        assert saved[field] == version_id
                        assert label not in saved["target_reference"]
                        assert tx.get("release_version", {"release_version_id": version_id})["release_version_id"] == version_id
        with database.engine.connect() as connection:
            for version_id, committed in results:
                assert connection.scalar(
                    text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"),
                    {"code": markers[version_id].encode("utf-8")},
                ) == int(committed)

        # 잠금 경합으로 취소된 작업은 결과를 확인한 뒤 새 트랜잭션에서 다시 실행한다.
        for version_id, committed in results:
            if not committed:
                with storage.transaction() as tx:
                    tx.insert("equipment", {"equipment_code": markers[version_id]})
                    tx.delete_release_version(str(version_id))
        with storage.transaction() as tx:
            saved = tx.get("risk_rule", {"risk_rule_id": risk["risk_rule_id"]})
            assert saved["target_reference"]["manual"] == "유지"
            assert saved["mapping_status"] == "UNRESOLVED"
            for version in versions:
                version_id = version["release_version_id"]
                for field, label in reference_fields[version_id].items():
                    assert saved[field] is None
                    assert saved["target_reference"][label] == {
                        name: version[name]
                        for name in ("release_version_id", "family_id", "version_raw", "version_suffix")
                    }
        for version_id in (first, second):
            with pytest.raises(LookupError):
                with storage.transaction() as tx:
                    tx.get("release_version", {"release_version_id": version_id})
    finally:
        database.dispose()


def test_real_disconnect_during_save_rolls_back_deletion_and_evidence(storage, kill_test_connection):
    """저장 도중 실제 연결을 끊으면 삭제·위험 근거·앞선 추가가 모두 취소되는지 검증한다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    - kill_test_connection: 테스트가 확보한 MySQL 연결을 종료하는 함수.
    """
    with storage.transaction() as tx:
        batch_id, _, family_id = graph(tx, "disconnect-during-save")
        item = tx.insert("item", {
            "content": "연결 중단 전 원본😀", "batch_id": batch_id,
            "source_reference": {"content": {"sheet": "릴리즈", "cells": ["B2"]}},
        })
        risk = tx.insert("risk_rule", {
            "rule_type": "DEFECT_ITEM", "family_id": family_id, "item_id": item["item_id"],
            "risk_reason": "접속 중단 검증", "mapping_status": "VERIFIED", "target_reference": {"manual": "유지"},
        })

    with pytest.raises(DatabaseSaveError) as caught:
        with storage.transaction() as tx:
            tx.insert("equipment", {"equipment_code": "DISCONNECT-DURING-SAVE"})
            tx.delete_release_item(str(item["item_id"]))
            connection_id = tx.session.scalar(text("SELECT CONNECTION_ID()"))
            kill_test_connection(connection_id)
            tx.session.execute(text("SELECT 1"))
    assert not isinstance(caught.value, DatabaseCommitUnknownError)

    with storage.transaction() as tx:
        assert tx.get("item", {"item_id": item["item_id"]}) == item
        assert tx.get("risk_rule", {"risk_rule_id": risk["risk_rule_id"]}) == risk
        tx.insert("equipment", {"equipment_code": "RECOVERED-AFTER-SAVE-DISCONNECT"})
    with storage.database.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"),
            {"code": b"DISCONNECT-DURING-SAVE"},
        ) == 0
        assert connection.scalar(
            text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"),
            {"code": b"RECOVERED-AFTER-SAVE-DISCONNECT"},
        ) == 1


def test_real_disconnect_at_commit_reports_unknown_without_retry(storage, kill_test_connection):
    """최종 저장 단계의 실제 연결 단절을 결과 미확인으로 알리고 재시도하지 않는지 검증한다.

    이 테스트는 COMMIT 전송 전에 연결을 끊는다. 저장 성공 후 응답만 유실되는
    상황을 재현하는 테스트는 아니다.

    param
    - storage: 테스트 DB의 공통 저장 서비스.
    - kill_test_connection: 테스트가 확보한 MySQL 연결을 종료하는 함수.
    """
    with storage.transaction() as tx:
        batch_id, _, family_id = graph(tx, "disconnect-at-commit")
        item = tx.insert("item", {"content": "최종 저장 전 원본😀", "batch_id": batch_id})
        risk = tx.insert("risk_rule", {
            "rule_type": "DEFECT_ITEM", "family_id": family_id, "item_id": item["item_id"],
            "risk_reason": "최종 저장 검증", "mapping_status": "VERIFIED", "target_reference": {"manual": "유지"},
        })

    commit_attempts = []

    def disconnect_before_commit(connection):
        """commit 시도 횟수를 기록하고 COMMIT 전송 전에 해당 연결을 종료한다.

        param
        - connection: 최종 저장을 시도하는 SQLAlchemy 연결.
        """
        # COMMIT 전송 직전에 실제 연결을 종료해 최종 저장 단계의 오류 분류를 확인한다.
        connection_id = connection.scalar(text("SELECT CONNECTION_ID()"))
        commit_attempts.append(connection_id)
        kill_test_connection(connection_id)

    event.listen(storage.database.engine, "commit", disconnect_before_commit)
    try:
        with pytest.raises(DatabaseCommitUnknownError):
            with storage.transaction() as tx:
                tx.insert("equipment", {"equipment_code": "DISCONNECT-AT-COMMIT"})
                tx.delete_release_item(str(item["item_id"]))
    finally:
        event.remove(storage.database.engine, "commit", disconnect_before_commit)
    assert len(commit_attempts) == 1

    # 이 재현에서는 COMMIT 전송 전에 연결을 종료했으므로 DB의 변경도 모두 취소된다.
    with storage.transaction() as tx:
        assert tx.get("item", {"item_id": item["item_id"]}) == item
        assert tx.get("risk_rule", {"risk_rule_id": risk["risk_rule_id"]}) == risk
        tx.insert("equipment", {"equipment_code": "RECOVERED-AFTER-COMMIT-DISCONNECT"})
    with storage.database.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"),
            {"code": b"DISCONNECT-AT-COMMIT"},
        ) == 0
        assert connection.scalar(
            text("SELECT COUNT(*) FROM equipment WHERE equipment_code = :code"),
            {"code": b"RECOVERED-AFTER-COMMIT-DISCONNECT"},
        ) == 1
