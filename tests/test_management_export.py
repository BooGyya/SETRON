"""BE-025의 저장된 현재 관리값·보관 원본·XLSX 출력을 실제 SQLite 자료로 확인한다."""

import json
from contextlib import closing, contextmanager
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from setron.features.export import management
from setron.features.export.management import (
    ManagementTable,
    build_management_workbook,
    read_management_data,
    save_management_data,
)
from setron.features.export.service import save_status_report
from setron.features.export.xlsx_cells import OVERFLOW_MARKER, OVERFLOW_SHEET
from setron.features.management import ManagementService
from setron.features.storage.validation import TABLE_NAMES, StorageValidationError
from setron.infrastructure.database import DatabaseConnectionError
from test_release_revision import all_rows, observation, release, revision_storage  # noqa: F401
from test_management_db_source import connected
from test_management_service import edit


def dictionaries(table):
    """컬럼 순서를 갖는 내보내기 표를 검증용 행 객체로 바꾼다."""
    return [dict(zip(table.columns, row)) for row in table.rows]


def test_read_is_one_transaction_and_returns_latest_management_values_with_originals(revision_storage):
    """수정 후 DB를 다시 읽되 가져온 원본과 다른 날짜의 자료를 바꾸지 않는다."""
    storage = revision_storage
    release(storage)
    snapshot_id, versions = observation(storage)
    observation(storage, day=date(2026, 10, 2))
    original_batches = all_rows(storage, "import_batch")
    original_sources = all_rows(storage, "equipment_version")[0]["source_reference"]
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": snapshot_id}, {"line": "수정한 Line"})
        tx.update("equipment_version", {"equipment_version_id": versions[0]["equipment_version_id"]},
                  {"version_raw": "v2"})

    class CountedStorage:
        count = 0

        @contextmanager
        def transaction(self):
            self.count += 1
            with storage.transaction() as tx:
                yield tx

    counted = CountedStorage()
    tables = read_management_data(counted)
    by_name = {table.name: table for table in tables}

    assert counted.count == 1
    assert tuple(by_name) == TABLE_NAMES
    assert dictionaries(by_name["import_batch"]) == original_batches
    snapshots = dictionaries(by_name["equipment_snapshot"])
    assert [row["line"] for row in snapshots] == ["수정한 Line", "Line1"]
    assert [row["version_raw"] for row in dictionaries(by_name["equipment_version"])] == ["v2", "v1"]
    assert dictionaries(by_name["equipment_version"])[0]["source_reference"] == original_sources
    assert by_name["risk_rule"].columns and by_name["risk_rule"].rows == ()


def test_xlsx_roundtrip_preserves_types_literal_strings_and_canonical_json(tmp_path, revision_storage):
    """수식처럼 보이는 원문·빈 문자열·JSON·날짜·ID가 XLSX에서도 같은 자료로 남는다."""
    storage = revision_storage
    snapshot_id, _ = observation(storage, code="=HYPERLINK(\"https://example.invalid\",\"호기\")")
    value = {"z": [None, True, "=1+2", "한글\n줄"], "a": {"empty": "", "number": 12345678901234567890}}
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": snapshot_id}, {"line": "", "model_name": "#N/A"})
        tx.insert("app_setting", {"setting_key": "설정", "setting_value": value})
    path = tmp_path / "관리.xlsx"

    result = save_management_data(storage, path)

    assert result.saved
    assert list(tmp_path.iterdir()) == [path]
    with closing(load_workbook(path)) as book:
        assert tuple(book.sheetnames) == TABLE_NAMES
        assert book["equipment"]["B2"].value.startswith("=HYPERLINK")
        assert book["equipment"]["B2"].data_type == "s"
        assert book["equipment"]["A2"].value == 1
        assert book["equipment"]["A2"].data_type == "n"
        snapshot = dict(zip((c.value for c in book["equipment_snapshot"][1]),
                            (c.value for c in book["equipment_snapshot"][2])))
        assert snapshot["base_date"] == date(2026, 10, 1)
        assert snapshot["line"] == ""
        assert snapshot["production_area"] is None
        assert snapshot["model_name"] == "#N/A"
        assert book["app_setting"]["B2"].value == json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        assert json.loads(book["app_setting"]["B2"].value) == value
        for sheet in book:
            assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref
            assert not any(cell.data_type == "f" for row in sheet for cell in row)


def test_empty_tables_still_include_their_headers(revision_storage, tmp_path):
    """행이 없는 테이블도 헤더가 있는 시트로 제공한다."""
    path = tmp_path / "빈관리.xlsx"
    assert save_management_data(revision_storage, path).saved
    with closing(load_workbook(path)) as book:
        assert book["equipment"].max_row == 1
        assert [c.value for c in book["equipment"][1]] == ["equipment_id", "equipment_code"]


@pytest.mark.parametrize("tables", [
    (),
    [ManagementTable("equipment", ("equipment_id",), ())],
    (ManagementTable("unknown", ("id",), ()),),
    (ManagementTable("equipment", (), ()),),
    (ManagementTable("equipment", ("id", "id"), ()),),
    (ManagementTable("equipment", ("id",), ((1, 2),)),),
    (ManagementTable("equipment", ("id",), [[1]]),),
    (ManagementTable("equipment", ("id",), ()), ManagementTable("equipment", ("id",), ())),
])
def test_malformed_tables_are_rejected(tables):
    """누락·중복·행 너비 오류가 있는 표는 파일 생성 전에 거부한다."""
    with pytest.raises(StorageValidationError):
        build_management_workbook(tables)


@pytest.mark.parametrize("value", ["금지\x00문자", float("nan"), float("inf"),
                                  1234567890123456, datetime(2026, 11, 3, microsecond=1)],
                         ids=["제어문자", "NaN", "무한대", "정수정밀도", "시간정밀도"])
def test_values_that_excel_cannot_preserve_fail_explicitly(value):
    """Excel 한도 밖의 값은 잘라내거나 다른 값으로 바꾸지 않는다."""
    with pytest.raises(StorageValidationError):
        build_management_workbook((ManagementTable("equipment", ("equipment_code",), ((value,),)),))


def test_exact_cell_limit_and_formula_text_survive_roundtrip(tmp_path):
    """허용 길이의 원문은 잘림 없이 남고 수식·오류 문자열은 텍스트로 남는다."""
    text = "=" + "한" * 32766
    book = build_management_workbook((ManagementTable("equipment", ("equipment_code",), ((text,), ("#N/A",))),))
    path = tmp_path / "한도.xlsx"
    book.save(path)
    book.close()
    with closing(load_workbook(path)) as saved:
        assert saved.active["A2"].value == text and saved.active["A2"].data_type == "s"
        assert saved.active["A3"].value == "#N/A" and saved.active["A3"].data_type == "s"


def test_failed_file_replacement_preserves_destination_and_removes_only_own_temp(
    revision_storage, tmp_path, monkeypatch,
):
    """마지막 파일 교체 실패에도 기존 파일과 무관한 임시파일을 유지한다."""
    path = tmp_path / "관리.xlsx"
    path.write_bytes(b"existing workbook")
    unrelated = tmp_path / ".관리.xlsx.other.tmp"
    unrelated.write_text("보존", encoding="utf-8")

    def fail_replace(source, destination):
        assert source.exists() and source != unrelated
        raise PermissionError("검증용 파일 사용 중")

    monkeypatch.setattr(management.os, "replace", fail_replace)
    result = save_management_data(revision_storage, path)

    assert not result.saved and result.message
    assert path.read_bytes() == b"existing workbook"
    assert set(tmp_path.iterdir()) == {path, unrelated}


def test_large_source_and_management_journal_json_roundtrip_without_loss(revision_storage, tmp_path):
    """긴 원본·수정 이력 JSON을 명시한 분할 순서로 전부 복원할 수 있다."""
    source = {"현황": {"A1": "원본😀\n" * 15000, "B1": "=1+2"}}
    journal = {"schema": 1, "management_edit": {"before": [
        {"table": "item", "content": "이전 관리값\n" * 13000},
    ], "after": [{"table": "item", "content": "수정한 관리값" * 13000}]}}
    with revision_storage.transaction() as tx:
        tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": source})
        tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": journal})
    path = tmp_path / "관리.xlsx"
    result = save_management_data(revision_storage, path)

    assert result.saved and list(tmp_path.iterdir()) == [path]
    with closing(load_workbook(path)) as book:
        assert tuple(book.sheetnames) == (*TABLE_NAMES, management.OVERFLOW_SHEET)
        parts = list(book[management.OVERFLOW_SHEET].iter_rows(min_row=2, values_only=True))
        for number, expected in enumerate((source, journal), 2):
            reference_id = f"import_batch!D{number}"
            assert book["import_batch"][f"D{number}"].value == management.OVERFLOW_MARKER + reference_id
            related = sorted((row for row in parts if row[0] == reference_id), key=lambda row: row[4])
            assert all(row[1:4] == ("import_batch", number, "raw_data") for row in related)
            assert [row[4] for row in related] == list(range(1, related[0][5] + 1))
            assert all(row[5] == len(related) and len(row[6]) <= 32767 for row in related)
            canonical = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            assert len(canonical) > 70000
            restored = "".join(row[6] for row in related)
            assert restored == canonical and json.loads(restored) == expected
        assert all(sheet.sheet_state == "visible" for sheet in book)
        assert not any(cell.data_type == "f" for sheet in book for row in sheet for cell in row)


def test_long_plain_strings_are_split_and_literal_reference_markers_are_unambiguous(tmp_path):
    """일반 긴 문자열과 참조 문구가 같은 실제 문자열을 좌표별 분할 자료로 구분한다."""
    text = "=" + "가" * 32766 + "=HYPERLINK(\"https://example.invalid\",\"원문\")" + "😀" * 40000
    literal_marker = management.OVERFLOW_MARKER + "equipment!A2"
    book = build_management_workbook((ManagementTable(
        "equipment", ("equipment_code",), ((text,), (literal_marker,)),
    ),))
    path = tmp_path / "긴원문.xlsx"
    book.save(path)
    book.close()
    with closing(load_workbook(path)) as saved:
        assert saved["equipment"]["A2"].value == saved["equipment"]["A3"].value == literal_marker
        parts = list(saved[management.OVERFLOW_SHEET].iter_rows(min_row=2, values_only=True))
        assert all(row[0] == "equipment!A2" and row[2] == 2 for row in parts)
        assert "".join(row[6] for row in parts) == text
        assert parts[1][6].startswith("=HYPERLINK")
        assert all(cell.data_type == "s" for cell in saved[management.OVERFLOW_SHEET]["G"][1:])


def test_long_plain_strings_still_reject_forbidden_control_characters():
    """긴 값 분할이 제어 문자 검사를 우회하지 않는다."""
    with pytest.raises(StorageValidationError, match="제어 문자"):
        build_management_workbook((ManagementTable("equipment", ("equipment_code",),
                                                  (("원문" * 20000 + "\x00",),)),))


def test_failed_workbook_write_preserves_existing_file_and_cleans_partial_temp(
    revision_storage, tmp_path, monkeypatch,
):
    """XLSX 기록 도중 실패해도 기존 파일과 새 파일이 뒤섞이지 않는다."""
    path = tmp_path / "관리.xlsx"
    path.write_bytes(b"existing workbook")

    def fail_save(book, temporary):
        temporary.write_bytes(b"partial workbook")
        raise OSError("검증용 기록 실패")

    monkeypatch.setattr(management.Workbook, "save", fail_save)
    result = save_management_data(revision_storage, path)

    assert not result.saved and "기록 실패" in result.message
    assert path.read_bytes() == b"existing workbook"
    assert list(tmp_path.iterdir()) == [path]


def test_failed_read_bad_extension_and_missing_folder_report_reasons(revision_storage, tmp_path):
    """DB 실패·확장자 오류·저장 폴더 없음이 서로 안내되며 부분 파일은 남기지 않는다."""
    class FailingStorage:
        def transaction(self):
            raise DatabaseConnectionError("연결 실패")

    result = save_management_data(FailingStorage(), tmp_path / "관리.xlsx")
    assert not result.saved and "DB 자료" in result.message
    assert not save_management_data(FailingStorage(), tmp_path / "관리.txt").saved
    assert not save_management_data(revision_storage, tmp_path / "없음" / "관리.xlsx").saved
    assert not list(tmp_path.iterdir())


def test_saved_edits_survive_current_status_xlsx_roundtrip_without_formula_or_text_loss(
    revision_storage, tmp_path,
):
    """실제 관리 저장→최신 현황 출력에서 설비명과 40,003자 위험 사유를 보존한다."""
    storage = revision_storage
    _, _, snapshot, risk_id = connected(storage)
    service = ManagementService(storage)
    original_batches = all_rows(storage, "import_batch")
    original_snapshot = service.get_record("equipment_snapshot", {"snapshot_id": snapshot.snapshot_id})
    reason = "=" + "A" * 32766 + "=1+2" + "B" * 7229 + "END"
    assert len(reason) == 40003
    service.save((
        edit(service, "equipment", {"equipment_id": snapshot.equipment_id}, {"equipment_code": "=1+1"}),
        edit(service, "risk_rule", {"risk_rule_id": risk_id}, {"risk_reason": reason}),
    ))
    value = service.current_report(snapshot.base_date)
    path = tmp_path / "수정현황.xlsx"
    result = save_status_report(value, path)

    assert result.saved and list(tmp_path.iterdir()) == [path]
    assert value.summary.equipment[0].record.equipment_code == "=1+1"
    assert service.get_record("risk_rule", {"risk_rule_id": risk_id}).current["risk_reason"] == reason
    assert all_rows(storage, "import_batch")[:len(original_batches)] == original_batches
    assert service.get_record("equipment_snapshot", {"snapshot_id": snapshot.snapshot_id}) == original_snapshot
    with closing(load_workbook(path)) as saved:
        assert saved["설비별 판정"]["A2"].value == "=1+1"
        assert saved["설비별 판정"]["A2"].data_type == "s"
        reference_id = "설비별 판정!L2"
        assert saved["설비별 판정"]["L2"].value == OVERFLOW_MARKER + reference_id
        parts = list(saved[OVERFLOW_SHEET].iter_rows(min_row=2, values_only=True))
        related = sorted((row for row in parts if row[0] == reference_id), key=lambda row: row[4])
        assert [row[:6] for row in related] == [
            (reference_id, "설비별 판정", 2, "위험 사유", 1, 2),
            (reference_id, "설비별 판정", 2, "위험 사유", 2, 2),
        ]
        assert "".join(row[6] for row in related) == reason
        assert all(row[6].startswith("=") for row in related)
        assert not any(cell.data_type == "f" for sheet in saved for row in sheet for cell in row)
