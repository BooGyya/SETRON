"""전용 MySQL 5.6.19 스키마에서 위치·계열·파일 범위와 맞춤 파일 저장을 검증한다."""

from contextlib import closing
from dataclasses import replace
from datetime import date
import os

from openpyxl import load_workbook
import pytest
from sqlalchemy import text

from setron.features.export import management
from setron.features.export.template import TemplateRow
from setron.features.export.template_workbook import read_workbook_records, save_workbook_template_export
from setron.features.storage.service import StorageService
from test_release_revision import all_rows
from test_schema_mysql import schema_database  # noqa: F401
from test_template_workbook import populate_scoped_storage, workbook_config  # noqa: F401


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                                reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_real_mysql_location_family_batch_intersection_and_workbook(
        schema_database, workbook_config, monkeypatch, tmp_path):
    storage = StorageService(schema_database)
    with schema_database.engine.connect() as connection:
        assert connection.scalar(text("SELECT VERSION()")).split("-")[0] == "5.6.19"
    with storage.transaction() as tx:
        for identifier, name, category in ((1, "O2", "OXIDE"), (3, "C3", "POLY")):
            tx.insert("equipment_group", {"group_id": identifier, "group_name": name, "category": category})
        tx.insert("version_family", {"family_id": 1, "group_id": 1, "family_name": "O2"})
        tx.insert("version_family", {"family_id": 2, "group_id": 3, "family_name": "C3"})
    _, release_batch = populate_scoped_storage(storage)
    snapshots = all_rows(storage, "equipment_snapshot")
    assert isinstance(snapshots[0]["base_date"], date)
    assert isinstance(snapshots[0]["site"], str)
    assert isinstance(snapshots[0]["snapshot_id"], int)
    releases = all_rows(storage, "release_version")
    assert all(isinstance(row["version_raw"], str) for row in releases)

    reads = []
    original = management.read_management_data

    def snapshot(*args):
        reads.append(1)
        return original(*args)

    monkeypatch.setattr(management, "read_management_data", snapshot)
    result = management.read_management_records_by_scope(storage, {
        "목표": {"site": "화성", "line": "13L"},
        "다른사업장": {"site": "평택", "line": "13L"},
        "소문자": {"line": "13l"},
        "충돌": {"production_area": "FOUNDRY"},
    }, production_area="MEMORY")
    assert len(reads) == 1
    assert [row["설비 호기"] for row in result["목표"]["설비현황표"]] == ["EQ-A"]
    assert {row["버전(원문)"] for row in result["목표"]["릴리즈노트"]} == {"v1", "v2"}
    for key in ("다른사업장", "소문자", "충돌"):
        assert result[key]["설비현황표"] == result[key]["릴리즈노트"] == ()

    first, second = workbook_config.sheets
    second = replace(second, production_area="MEMORY", site="화성", line="13L",
                     template=replace(second.template, rows=(TemplateRow(1, "버전(원문)"), TemplateRow(2, "내용"))))
    config = replace(workbook_config, sheets=(replace(first, site="화성"), second))
    reads.clear()
    selected = read_workbook_records(storage, config, batch_ids=(release_batch,))
    assert len(reads) == 1
    assert selected["13L"]["설비현황표"] == ()
    assert [row["버전(원문)"] for row in selected["Foundry"]["릴리즈노트"]] == ["v2"]
    assert selected["Foundry"]["_scope_choices"]["sites"] == ("평택", "화성")
    reads.clear()
    output = tmp_path / "mysql_통합.xlsx"
    saved = save_workbook_template_export(storage, output, config, batch_ids=(release_batch,))
    assert saved.saved and len(reads) == 1
    with closing(load_workbook(output)) as book:
        assert book["13L"]["A2"].value is None
        assert book["13L"]["C2"].value == "사용자 메모"
        assert book["Foundry"]["B1"].value == "v2"
        assert book["Foundry"]["C1"].value is None
        assert book["Foundry"]["B3"].value == "=LEN(B2)"
