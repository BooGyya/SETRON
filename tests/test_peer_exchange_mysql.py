"""실제 MySQL SQL 내보내기가 교환 UUID와 변경 이벤트를 함께 보존한다."""

import os
from uuid import UUID

import pytest
from sqlalchemy import MetaData

from setron.features.db_export.service import export_database
from setron.features.db_import.dump import read_dump
from setron.features.db_import.service import SqlImportService
from setron.features.peer_sync import read_state

from test_schema_mysql import schema_database  # noqa: F401
from test_sql_import_service import bundle, current, seed


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                                reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_mysql_peer_sql_preserves_identity_history_and_does_not_replay_old_revision(schema_database, tmp_path):
    """내보내기·다시 내보내기·로컬 변경·과거 파일 검토까지 실제 DB에서 검증한다."""
    database = schema_database
    metadata = MetaData()
    metadata.reflect(database.engine)
    peer_id = str(UUID(int=101))
    service = SqlImportService(lambda: type(database)(database.engine), peer_id=peer_id)
    peer = database.engine, metadata.tables, service
    seed(peer, bundle())

    path = tmp_path / "동등 교환.sql"
    assert export_database(database, path).saved
    source = read_dump(path)
    initial = read_state(source)
    assert initial.entities and initial.events
    original_ids = set(initial.entities)
    original_events = set(initial.events)
    assert export_database(database, path).saved
    repeated = read_state(read_dump(path))
    assert set(repeated.entities) == original_ids and set(repeated.events) == original_events

    with database.engine.begin() as connection:
        connection.execute(metadata.tables["equipment_snapshot"].update().values(line="이 PC의 새 라인"))
    latest_path = tmp_path / "변경 뒤 교환.sql"
    assert export_database(database, latest_path).saved
    latest = read_state(read_dump(latest_path))
    assert set(latest.entities) == original_ids
    assert original_events < set(latest.events)
    before = current(peer)
    old_plan = service.preview(path, "review")
    assert old_plan.unresolved == 0
    service.apply(old_plan)
    assert current(peer) == before
