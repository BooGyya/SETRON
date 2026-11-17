"""MySQL 5.6.19와 다른 PC의 SQL 교환에서 자동 병합·삭제 확인을 검증한다."""

import os
from copy import deepcopy

import pytest
from sqlalchemy import MetaData

from setron.features.data_admin.service import DataAdminService
from setron.features.db_export.service import export_database
from setron.features.db_import.service import SqlImportService
from setron.features.peer_sync import identity_index, key_token, read_state
from setron.features.storage.service import StorageService
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import Database
from test_peer_exchange import PEERS, choose_all, delete_snapshot, exchange_file, peers, stamp  # noqa: F401
from test_schema_mysql import schema_database  # noqa: F401
from test_sql_import_service import bundle, current, seed


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                                reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


@pytest.fixture
def mysql_peer(schema_database):
    metadata = MetaData()
    metadata.reflect(schema_database.engine, only=TABLE_NAMES)
    with schema_database.engine.begin() as connection:
        for name in reversed(TABLE_NAMES):
            connection.execute(metadata.tables[name].delete())
    peer = (schema_database.engine, metadata.tables,
            SqlImportService(lambda: Database(schema_database.engine), peer_id=PEERS[0]))
    seed(peer, bundle(line="공통 라인"))
    return peer


def share(mysql_peer, peers, tmp_path):
    database = Database(mysql_peer[0])
    path = tmp_path / "공통 MySQL.sql"
    assert export_database(database, path, peer_id=PEERS[0]).saved
    remote = peers("remote")
    remote[2].apply(choose_all(remote[2], remote[2].preview(path, "review")))
    return remote


def test_mysql_auto_merge_preserves_live_local_edit_and_applies_remote_field(mysql_peer, peers, tmp_path):
    """이력을 아직 내보내지 않은 MySQL의 라인은 보존하고 다른 PC의 공정을 자동 반영한다."""
    remote = share(mysql_peer, peers, tmp_path)
    with mysql_peer[0].begin() as connection:
        connection.execute(mysql_peer[1]["equipment_snapshot"].update().values(line="MySQL의 라인"))
    with remote[0].begin() as connection:
        connection.execute(remote[1]["equipment_snapshot"].update().values(process_name="다른 PC의 공정"))
    stamp(remote, PEERS[1])
    path = exchange_file(remote, tmp_path / "자동 병합.sql")
    service = mysql_peer[2]
    plan = service.preview(path, "review")
    assert plan.unresolved == 0 and any(item.automatic for item in plan.differences)
    service.apply(plan)
    stored, = current(mysql_peer)["equipment_snapshot"]
    assert (stored["line"], stored["process_name"]) == ("MySQL의 라인", "다른 PC의 공정")
    before = deepcopy(current(mysql_peer))
    service.apply(service.preview(path, "review"))
    assert current(mysql_peer) == before


def test_mysql_divergent_edit_requires_choice_and_records_resolution(mysql_peer, peers, tmp_path):
    """같은 필드의 서로 다른 수정은 MySQL에서도 무검토 저장하지 않는다."""
    remote = share(mysql_peer, peers, tmp_path)
    for peer, value in ((mysql_peer, "MySQL 변경"), (remote, "다른 PC 변경")):
        with peer[0].begin() as connection:
            connection.execute(peer[1]["equipment_snapshot"].update().values(line=value))
    stamp(remote, PEERS[1])
    service = mysql_peer[2]
    plan = service.preview(exchange_file(remote, tmp_path / "충돌.sql"), "review")
    assert plan.unresolved > 0
    before = deepcopy(current(mysql_peer))
    with pytest.raises(ValueError, match="선택"):
        service.apply(plan)
    assert current(mysql_peer) == before
    service.apply(choose_all(service, plan, "CURRENT"))
    assert current(mysql_peer)["equipment_snapshot"][0]["line"] == "MySQL 변경"
    assert any(event["operation"] == "resolve" for event in read_state(current(mysql_peer)).events.values())


def test_mysql_one_sided_delete_still_requires_individual_confirmation(mysql_peer, peers, tmp_path):
    """공통 기준이 있어도 실제 삭제는 사용자 확인 전까지 MySQL 자료를 유지한다."""
    remote = share(mysql_peer, peers, tmp_path)
    delete_snapshot(remote)
    stamp(remote, PEERS[1])
    service = mysql_peer[2]
    plan = service.preview(exchange_file(remote, tmp_path / "삭제 확인.sql"), "review")
    assert plan.unresolved > 0
    assert all(item.choice is None for item in plan.differences if item.field == "삭제")
    before = deepcopy(current(mysql_peer))
    with pytest.raises(ValueError, match="선택"):
        service.apply(plan)
    assert current(mysql_peer) == before
    service.apply(choose_all(service, plan, "INCOMING"))
    assert not current(mysql_peer)["equipment_snapshot"]
    assert not current(mysql_peer)["equipment_pm"]


def test_mysql_restore_keeps_uuid_across_new_autoincrement_id_and_peer_exchange(mysql_peer, peers, tmp_path):
    """MySQL의 새 복원 PK에도 원래 UUID를 유지하고 다른 PC의 최신 자료를 지우지 않는다."""
    remote = share(mysql_peer, peers, tmp_path)
    original = identity_index(current(mysql_peer))["equipment_snapshot", key_token({"snapshot_id": 1})]
    admin = DataAdminService(StorageService(Database(mysql_peer[0])))
    journal = admin.delete_rows(1, [1])
    stamp(mysql_peer, PEERS[0])
    assert admin.restore_rows(journal) == 1
    stamp(mysql_peer, PEERS[0])
    restored, = current(mysql_peer)["equipment_snapshot"]
    assert restored["snapshot_id"] > 1
    assert identity_index(current(mysql_peer))[
        "equipment_snapshot", key_token({"snapshot_id": restored["snapshot_id"]})] == original
    path = tmp_path / "MySQL 복원 교환.sql"
    assert export_database(Database(mysql_peer[0]), path, peer_id=PEERS[0]).saved
    service = remote[2]
    plan = service.preview(path, "review")
    service.apply(choose_all(service, plan, "INCOMING") if plan.unresolved else plan)
    assert len(current(remote)["equipment_snapshot"]) == 1
    assert len(current(remote)["equipment_pm"]) == 1


@pytest.mark.parametrize("choice,remaining", [("CURRENT", 1), ("INCOMING", 0)])
def test_mysql_child_deletion_requires_confirmation_and_respects_choice(mysql_peer, peers, tmp_path, choice, remaining):
    """다른 PC의 PM 삭제도 자동 적용하지 않고 선택한 결과와 재가져오기를 보존한다."""
    remote = share(mysql_peer, peers, tmp_path)
    with remote[0].begin() as connection:
        connection.execute(remote[1]["equipment_pm"].delete())
    stamp(remote, PEERS[1])
    service = mysql_peer[2]
    path = exchange_file(remote, tmp_path / "PM 삭제 확인.sql")
    plan = service.preview(path, "review")
    deletion, = [item for item in plan.differences if item.field == "PM·SW 구성"]
    assert deletion.requires_confirmation and not deletion.automatic and deletion.choice is None
    before = deepcopy(current(mysql_peer))
    with pytest.raises(ValueError, match="선택"):
        service.apply(plan)
    assert current(mysql_peer) == before
    service.apply(choose_all(service, plan, choice))
    assert len(current(mysql_peer)["equipment_snapshot"]) == 1
    assert len(current(mysql_peer)["equipment_pm"]) == remaining
    after = deepcopy(current(mysql_peer))
    service.apply(service.preview(path, "review"))
    assert current(mysql_peer) == after
