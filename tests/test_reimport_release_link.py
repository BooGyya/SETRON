"""공유 SW의 확인된 릴리즈 연결은 PM별 원본 출처와 독립적으로 반영한다."""

import pytest

from setron.features.excel_import.service import ImportValidationService
from setron.features.import_save.service import DatabaseImportSaver
from test_import_validation import draft, row
from test_snapshot_reimport import storage, save, snapshot  # noqa: F401


def test_confirmed_alias_updates_shared_release_id_without_replacing_untouched_pm_source(storage):
    save(storage, row(version_raw="v1_확인전"), row(2, pm_name="PM2", version_raw="v1_확인전"))
    before = snapshot(storage)
    original_sw = before.software[0]
    untouched = original_sw.pms[1]
    assert original_sw.release_version_id is None
    source = draft(row(version_raw="v1_확인전"), sheet="확정 대응 검토")
    request, = ImportValidationService(storage).prepare((source,), "confirmed-alias",
                                                       aliases={1: {"v1_확인전": "v1"}})
    assert request.rows[0].release_version_id == 1
    DatabaseImportSaver(storage).save(request)
    after = snapshot(storage)
    assert after.software[0].release_version_id == 1
    assert after.software[0].batch_id == original_sw.batch_id
    assert after.software[0].source_reference == original_sw.source_reference
    assert after.software[0].pms[1] == untouched


@pytest.mark.parametrize("names", [("PM1",), ("PM1", "PM2")])
def test_full_reimport_without_alias_does_not_forget_a_confirmed_link(storage, names):
    source = draft(*(row(index, pm_name=name, version_raw="v1_확인됨")
                     for index, name in enumerate(names, 1)))
    request, = ImportValidationService(storage).prepare((source,), "confirmed-first",
                                                       aliases={1: {"v1_확인됨": "v1"}})
    DatabaseImportSaver(storage).save(request)
    assert snapshot(storage).software[0].release_version_id == 1
    without_alias, = ImportValidationService(storage).prepare((source,), "repeat-without-alias")
    assert without_alias.rows[0].release_version_id is None
    DatabaseImportSaver(storage).save(without_alias)
    assert snapshot(storage).software[0].release_version_id == 1
