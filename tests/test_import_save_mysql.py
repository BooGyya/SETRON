"""가져오기 묶음의 DB 저장을 실제 MySQL 5.6.19 스키마와 시연용 기준 자료로 검증한다 (Jira S15P31B106-75)."""

import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pymysql
import pytest
from pymysql.constants import CLIENT
from sqlalchemy import func, select

from setron.features.ai.schemas import ExtractKind
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import DuplicateSaveError, SaveRequest, SaveRow
from setron.features.excel_import.service import ImportValidationService, build_save_request
from setron.features.import_save.service import DatabaseImportSaver, batch_raw_data
from setron.features.release.service import ReleaseService
from setron.features.release.schemas import ReleaseInput
from setron.features.storage.service import StorageService
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.ai_client import ResponseSource
from setron.infrastructure.database import DatabaseConfig
from test_schema_mysql import schema_database  # noqa: F401  공통 스키마 생성·정리 fixture 재사용
from test_import_validation import draft as review_draft, row as review_row

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")

SEED = Path(__file__).resolve().parents[1] / "database" / "seed_demo_catalog.sql"
SHEET = "현황"


@pytest.fixture(scope="module")
def storage(schema_database):  # noqa: F811
    """스키마에 시연용 기준 자료를 두 번 적용한다 (두 번째는 아무것도 넣지 않아야 한다)."""
    config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
    for _ in range(2):
        connection = pymysql.connect(host=config.host, port=config.port, user=config.username,
                                     password=config.password, database=config.database, charset="utf8mb4",
                                     client_flag=CLIENT.MULTI_STATEMENTS)
        try:
            with connection.cursor() as cursor:
                cursor.execute(SEED.read_text(encoding="utf-8"))
                while cursor.nextset():
                    pass
            connection.commit()
        finally:
            connection.close()
    return StorageService(schema_database)


def count(storage, table, **filters):
    with storage.transaction() as tx:
        target = tx.repository.table(table)
        return tx.session.scalar(select(func.count()).select_from(target).filter_by(**filters))


def row(row_id, cells, edited=(), **values):
    """원본 출처와 사용자가 직접 보완한 메타데이터를 구분한 합성 검토 행을 만든다.

    param
    - row_id, cells: 검토 행 ID와 항목별 실제 원본 셀 주소.
    - edited: 원본과 다르게 명시적으로 수정한 항목.
    - values: 검토를 마친 현재 값. 출처가 없는 메타데이터는 사용자가 직접 입력한 값.

    return
    - 원본 대조 또는 명시적 보완 근거를 가진 SaveRow.
    """
    sources = {name: {"sheet": SHEET, "cells": address} for name, address in cells.items()}
    manual = {name for name, value in values.items() if value is not None and name not in cells}
    return SaveRow((row_id,), values, sources or None, tuple(sorted(set(edited) | manual)))


def request(kind, rows, request_id, cells=None):
    """현재 값에 실제로 대응하는 합성 원본을 만들되 명시한 원본은 그대로 보존한다.

    param
    - kind, rows, request_id: 자료 종류·검토 행·요청 식별자.
    - cells: 별도 원본. None이면 출처가 지정된 실제 시험값으로 구성.

    return
    - 원문과 보완 항목을 구분한 저장 요청.
    """
    raw_cells, prepared = ({} if cells is None else dict(cells)), []
    for value in rows:
        edited = set(value.edited_fields)
        for name, source in (value.source_reference or {}).items():
            addresses = source["cells"] if isinstance(source["cells"], list) else source["cells"].split(",")
            for address in addresses:
                if cells is None:
                    if address not in raw_cells:
                        raw_cells[address] = value.values.get(name)
                    elif raw_cells[address] != value.values.get(name):
                        # 같은 원본 셀에 다른 관리값을 준 합성 사례는 명시적 보완으로 구분한다.
                        edited.add(name)
        prepared.append(replace(value, edited_fields=tuple(sorted(edited))))
    raw = {"sheet": SHEET, "cells": raw_cells, "merged": ["A1:A2"]}
    return SaveRequest(request_id, kind, "가상.xlsx", raw, tuple(prepared), 0, ResponseSource.EXAMPLE)


EQUIPMENT = dict(base_date="2026-10-21", site="화성", line="13L", production_area="MEMORY")


def mixed_rows(code="QA-MIXED"):
    """PM1에 O3, PM3에 O2를 쓰는 혼합 설비 행 두 개."""
    base = dict(EQUIPMENT, equipment_code=code, model_name="M-O3/O2 혼합", group_name="Oxide")
    return [
        row(1, {"equipment_code": "C5", "site": "A1", "version_raw": "F5", "pm_name": "E5"},
            **base, pm_name="PM1", family_name="O3", version_raw="v1.2"),
        row(2, {"equipment_code": "C5", "version_raw": "F6", "pm_name": "E6"},
            **base, pm_name="PM3", family_name="O2", version_raw="v2.0"),
    ]


def test_seed_catalog_has_models_and_families_once(storage):
    # 시드 v2: 멘토 확정 분류표의 모델 14개·계열 15개 (docs/mentor-confirmations.md)
    assert count(storage, "equipment_group") == 14
    assert count(storage, "version_family") == 15
    assert count(storage, "version_family", family_name="O2 (파운드리)") == 1
    assert count(storage, "equipment_group", group_name="HPA", analysis_enabled=0) == 1


def test_mixed_equipment_is_saved_with_batch_sources_and_pm_models(storage):
    rows = [replace_values(r, model_name=m) for r, m in zip(mixed_rows(), ("M-O3", "M-O2"))]
    saver = DatabaseImportSaver(storage)

    receipt = saver.save(request(ExtractKind.EQUIPMENT_STATUS, rows, "req-mixed"))

    with storage.transaction() as tx:
        batch = tx.get("import_batch", {"batch_id": receipt.batch_id})
    assert batch["source_type"] == "EXCEL" and set(batch["raw_data"]) == {SHEET}
    saved = EquipmentService(storage).get_snapshot("QA-MIXED", date(2026, 10, 21))
    assert saved.group_id is None and saved.site == "화성" and saved.production_area == "MEMORY"
    assert [(sw.version_raw, [pm.pm_name for pm in sw.pms]) for sw in saved.software] == [("v1.2", ["PM1"]), ("v2.0", ["PM3"])]
    assert saved.software[0].source_reference == {"version_raw": {"sheet": SHEET, "cells": ["F5"]}}
    assert all(sw.release_version_id is None for sw in saved.software)
    assert receipt.target_label.startswith("MySQL")


def test_release_is_saved_and_later_equipment_links_to_it(storage):
    saver = DatabaseImportSaver(storage)
    rows = [row(i, {"version_raw": "A2", "content": f"D{i + 1}", "item_no": f"C{i + 1}"},
                version_raw="v4.56.6.43", release_date="2026-09-01", item_no=i, content=f"변경 {i}",
                model_name="M-C4", family_name="C4", target_models="C4") for i in (1, 2)]
    receipt = saver.save(request(ExtractKind.RELEASE_NOTE, rows, "req-release"))

    releases = ReleaseService(storage).list_releases(_family_id(storage, "C4"))
    assert [r.current["version_raw"] for r in releases] == ["v4.56.6.43"]
    assert releases[0].current["released_at"] == date(2026, 9, 1)
    assert count(storage, "item", batch_id=receipt.batch_id) == 2

    equipment = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-C4",
                    model_name="M-C4", family_name="C4", version_raw="v4.56.6.43")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [equipment], "req-c4"))
    saved = EquipmentService(storage).get_snapshot("QA-C4", date(2026, 10, 21))
    assert saved.group_id == _group_id(storage, "M-C4")
    assert saved.software[0].release_version_id == releases[0].current["release_version_id"]


def test_c3_family_is_chosen_by_ui_and_m5_alias(storage):
    saver = DatabaseImportSaver(storage)
    c3 = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-C3",
             model_name="M-C3", family_name="C3", ui_type="통합UI", version_raw="v3.52_13 (H)")
    m5 = row(2, {"equipment_code": "C6", "version_raw": "F6"}, **EQUIPMENT, equipment_code="QA-M5",
             model_name="M5", family_name="O5", version_raw="v1060")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [c3, m5], "req-c3"))

    c3_saved = EquipmentService(storage).get_snapshot("QA-C3", date(2026, 10, 21))
    assert c3_saved.software[0].family_id == _family_id(storage, "C3 통합UI")
    assert c3_saved.software[0].version_raw == "v3.52_13 (H)"
    m5_saved = EquipmentService(storage).get_snapshot("QA-M5", date(2026, 10, 21))
    assert m5_saved.group_id == _group_id(storage, "M-O5") and m5_saved.model_name == "M5"


def test_foundry_line_uses_foundry_family_and_kctc_ox_is_saved(storage):
    """파운드리 라인의 C3·O2는 ' (파운드리)' 계열, O3는 공통 계열, KCTC Cluster Ox는 등록 모델로 저장한다.

    근거: 멘토 확정 10/14·10/27(파운드리용 릴리즈노트는 O2·C3·C4만 별도), 10/29(KCTC 4개 분리).
    """
    saver = DatabaseImportSaver(storage)
    foundry = dict(base_date="2026-10-21", site="평택", line="S5")       # production_area 를 비워 장소표 기본값을 쓴다
    c3 = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **foundry, equipment_code="QA-S5-C3",
             model_name="M-C3", family_name="C3", ui_type="통합UI", version_raw="v3.52_F15")
    o2 = row(2, {"equipment_code": "C6", "version_raw": "F6"}, **foundry, equipment_code="QA-S5-O2",
             model_name="M-O2", family_name="O2", version_raw="v3.53_F1")
    o3 = row(3, {"equipment_code": "C7", "version_raw": "F7"}, **foundry, equipment_code="QA-S5-O3",
             model_name="M-O3", family_name="O3", version_raw="v4.50.1.2")
    # 한 통합문서는 한 사업장이어야 하므로(사업장 혼재 검사) Cluster Ox도 평택 S5로 둔다. KCTC는 메모리/파운드리 공통 계열.
    ox = row(4, {"equipment_code": "C8", "version_raw": "F8"}, **foundry, equipment_code="QA-CLUSTER-OX",
             group_name="KCTC", model_name="Cluster Ox", family_name="Cluster Ox", structure_type="CLUSTER",
             version_raw="v999.3_7")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [c3, o2, o3, ox], "req-foundry"))

    service = EquipmentService(storage)
    day = date(2026, 10, 21)
    assert service.get_snapshot("QA-S5-C3", day).software[0].family_id == _family_id(storage, "C3 통합UI (파운드리)")
    assert service.get_snapshot("QA-S5-O2", day).software[0].family_id == _family_id(storage, "O2 (파운드리)")
    assert service.get_snapshot("QA-S5-O3", day).software[0].family_id == _family_id(storage, "O3")
    saved_ox = service.get_snapshot("QA-CLUSTER-OX", day)
    assert saved_ox.group_id == _group_id(storage, "Cluster Ox")
    assert saved_ox.software[0].family_id == _family_id(storage, "Cluster Ox")


def test_c3_without_ui_is_saved_as_integrated(storage):
    """UI 표기가 없는 M-C3는 통합UI 계열로 저장한다 (멘토 확정 2026-10-28)."""
    saver = DatabaseImportSaver(storage)
    c3 = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-C3-NOUI",
             model_name="M-C3", family_name="C3", version_raw="v3.52_22")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [c3], "req-c3-noui"))
    saved = EquipmentService(storage).get_snapshot("QA-C3-NOUI", date(2026, 10, 21))
    assert saved.software[0].family_id == _family_id(storage, "C3 통합UI")


@pytest.mark.parametrize("changes, message", [
    ({"model_name": "M-C9", "group_name": "Poly"}, "등록 모델이 없습니다"),
    ({"model_name": "M-C9"}, "등록 모델이 없습니다"),
    ({"group_name": "Etch"}, "설비군"),
    ({"group_name": "Poly", "model_name": "M-O2", "family_name": "O2"}, "소속이 다릅니다"),
    ({"model_name": "M-C3", "family_name": "C3", "ui_type": "신UI"}, "UI"),   # 미표기는 통합UI 기본값(10/28), 잘못된 UI 값만 막는다
    ({"family_name": "O9"}, "SW 계열이 없거나 여러 개"),
])
def test_unresolved_links_cancel_whole_batch(storage, changes, message):
    batches = count(storage, "import_batch")
    good = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-OK",
               model_name="M-O2", family_name="O2", version_raw="v1")
    bad = row(2, {"equipment_code": "C6", "version_raw": "F6"},
              **{**EQUIPMENT, "equipment_code": "QA-BAD", "model_name": "M-O2", "family_name": "O2",
                 "version_raw": "v1", **changes})

    with pytest.raises(StorageValidationError, match=message):
        DatabaseImportSaver(storage).save(request(ExtractKind.EQUIPMENT_STATUS, [good, bad], f"req-{message}"))

    assert count(storage, "import_batch") == batches
    assert EquipmentService(storage).get_snapshot("QA-OK", date(2026, 10, 21)) is None


def test_unreviewed_snapshot_and_existing_release_are_not_replaced(storage):
    saver = DatabaseImportSaver(storage)
    first = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-AGAIN",
                model_name="M-O2", family_name="O2", version_raw="v1")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [first], "req-again-1"))
    with pytest.raises(StorageValidationError, match="기존값을 불러와 검토"):
        saver.save(request(ExtractKind.EQUIPMENT_STATUS, [first], "req-again-2"))

    release = row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v9", content="내용",
                  model_name="M-O2", family_name="O2")
    saver.save(request(ExtractKind.RELEASE_NOTE, [release], "req-rel-1"))
    with pytest.raises(StorageValidationError, match="이미 저장된 릴리즈"):
        saver.save(request(ExtractKind.RELEASE_NOTE, [release], "req-rel-2"))


def test_edited_fields_keep_only_release_and_item_columns(storage):
    """검토 화면에서 고친 계열·모델은 연결에만 쓰고, 출시일·내용 수정만 DB 수정 표시로 남긴다."""
    value = row(1, {"version_raw": "A2", "content": "D2", "release_date": "B2"},
                edited=("family_name", "model_name", "release_date", "content"),
                version_raw="v-edit", release_date="2026-09-02", content="고친 내용", model_name="M-O3", family_name="O3")
    DatabaseImportSaver(storage).save(request(ExtractKind.RELEASE_NOTE, [value], "req-edited"))

    detail, = ReleaseService(storage).list_releases(_family_id(storage, "O3"))
    assert detail.current["edited_fields"] == ["released_at"]
    with storage.transaction() as tx:
        item, = EquipmentService(storage).repository.rows(tx, "item", content="고친 내용")
    assert item["edited_fields"] == ["content"]


def test_release_without_family_is_rejected_with_guidance(storage):
    rows = [row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v1.0.3", content="Recipe 승인 상태 확인")]
    with pytest.raises(StorageValidationError, match="모델명이 필요"):
        DatabaseImportSaver(storage).save(request(ExtractKind.RELEASE_NOTE, rows, "req-no-family"))


def test_same_request_is_not_saved_twice(storage):
    saver = DatabaseImportSaver(storage)
    value = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-ONCE",
                model_name="M-O2", family_name="O2", version_raw="v1")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [value], "req-once"))
    with pytest.raises(DuplicateSaveError):
        saver.save(request(ExtractKind.EQUIPMENT_STATUS, [value], "req-once"))


def _group_id(storage, name):
    with storage.transaction() as tx:
        return EquipmentService(storage).repository.rows(tx, "equipment_group", group_name=name)[0]["group_id"]


def _family_id(storage, name):
    with storage.transaction() as tx:
        return EquipmentService(storage).repository.rows(tx, "version_family", family_name=name)[0]["family_id"]


def replace_values(value, **changes):
    """검토 연결 ID와 출처를 유지하면서 합성 저장 행의 값만 교체한다.

    param
    - value: 교체 전 저장 요청 행.
    - changes: 교체할 공통 항목 값.

    return
    - 기존 메타데이터를 보존한 새 저장 요청 행.
    """
    return replace(value, values={**value.values, **changes})


def test_saver_is_created_only_with_db_settings():
    assert DatabaseImportSaver.from_env({}) is None
    saver = DatabaseImportSaver.from_env({"SETRON_DB_NAME": "setron"})
    assert saver is not None and saver._storage is None  # 첫 저장 전에는 접속하지 않는다


def test_raw_data_is_stored_as_sheet_cell_map():
    raw = {"sheet": "Memory", "cells": {"A1": "화성", "B5": 13}, "merged": ["A1:B1"]}
    assert batch_raw_data(raw) == {"Memory": {"A1": "화성", "B5": 13}}


@pytest.mark.parametrize("kind", [ExtractKind.EQUIPMENT_STATUS, ExtractKind.RELEASE_NOTE])
def test_pipeline_preserves_c3_ui_in_request_and_database(storage, kind):
    """실제 검토 요청 생성부터 저장·재조회까지 C3 두 UI의 자료와 출처를 각각 보존한다.

    param
    - storage: 공통 스키마와 시연용 모델·계열을 등록한 테스트 저장 서비스.
    - kind: 설비 현황 또는 릴리즈 노트.
    """
    source = review_draft(
        review_row(group_name="POLY", model_name="M-C3", family_name="C3 구UI", equipment_code="QA-PIPE-C3-LEGACY",
                   version_raw="v-c3-pipeline", item_no="41", content="같은 Item 원문"),
        review_row(2, group_name="POLY", model_name="M-C3", family_name="C3 통합UI", equipment_code="QA-PIPE-C3-INTEGRATED",
                   version_raw="v-c3-pipeline", item_no="41", content="같은 Item 원문"), kind=kind,
    )
    built = build_save_request(source, f"pipeline-c3-{kind.value}")
    assert [value.values["ui_type"] for value in built.rows] == ["LEGACY", "INTEGRATED"]
    assert [value.row_ids for value in built.rows] == [(1,), (2,)]
    DatabaseImportSaver(storage).save(built)
    for index, family_name in enumerate(("C3 구UI", "C3 통합UI")):
        family_id = _family_id(storage, family_name)
        if kind is ExtractKind.EQUIPMENT_STATUS:
            saved = EquipmentService(storage).get_snapshot(built.rows[index].values["equipment_code"], date(2026, 11, 2))
            assert saved.software[0].family_id == family_id
            assert saved.software[0].source_reference["version_raw"] == built.rows[index].source_reference["version_raw"]
        else:
            saved = next(value for value in ReleaseService(storage).list_releases(family_id)
                         if value.current["version_raw"] == "v-c3-pipeline")
            assert saved.current["source_reference"]["version_raw"] == built.rows[index].source_reference["version_raw"]
            assert len(ReleaseService(storage).get_release(saved.current["release_version_id"]).items) == 1


def test_single_c3_candidate_of_another_ui_is_rejected(storage):
    """모델의 후보가 하나뿐이어도 요청 UI와 다른 계열에 연결하지 않고 전체 저장을 취소한다.

    param
    - storage: 단일 UI 모델을 등록할 전용 테스트 저장 서비스.
    """
    with storage.transaction() as tx:
        model = tx.insert("equipment_group", {"group_name": "QA-C3 단일 UI 모델", "category": "POLY"})
        tx.insert("version_family", {"group_id": model["group_id"], "family_name": "C3", "ui_type": "LEGACY"})
    source = review_draft(review_row(group_name="POLY", model_name="QA-C3 단일 UI 모델", family_name="C3 통합UI",
                                    equipment_code="QA-PIPE-C3-WRONG", version_raw="v-single-ui"))
    built = build_save_request(source, "pipeline-c3-wrong-ui")
    assert built.rows[0].values["ui_type"] == "INTEGRATED"
    batches = count(storage, "import_batch")
    with pytest.raises(StorageValidationError, match="SW 계열이 없거나 여러 개"):
        DatabaseImportSaver(storage).save(built)
    assert count(storage, "import_batch") == batches
    assert EquipmentService(storage).get_snapshot("QA-PIPE-C3-WRONG", date(2026, 11, 2)) is None


@pytest.mark.parametrize("csv_sources", [False, True], ids=["목록형 출처", "기존 CSV 출처"])
def test_pipeline_combines_all_pm_software_and_snapshot_sources(storage, csv_sources):
    """요청 생성 후 목록·CSV 출처를 저장하고 같은 SW의 모든 PM 원본 위치를 다시 조회한다.

    param
    - storage: 실제 공통 스키마를 사용하는 테스트 저장 서비스.
    - csv_sources: 기존 CSV 형식의 요청과 새 목록 형식 중 검사할 계약.
    """
    code = f"QA-PIPE-SOURCE-{int(csv_sources)}"
    source = review_draft(review_row(equipment_code=code, version_raw="v-source-pipeline"),
                          review_row(2, equipment_code=code, pm_name="PM2", version_raw="v-source-pipeline"))
    built = build_save_request(source, f"pipeline-sources-{csv_sources}")
    if csv_sources:
        built = replace(built, rows=tuple(replace(value, source_reference={
            name: {"sheet": ref["sheet"], "cells": ",".join(ref["cells"])}
            for name, ref in value.source_reference.items()
        }) for value in built.rows))
    receipt = DatabaseImportSaver(storage).save(built)
    saved = EquipmentService(storage).get_snapshot(code, date(2026, 11, 2))
    assert len(saved.software) == 1 and len(saved.software[0].pms) == 2
    assert saved.software[0].source_reference["version_raw"] == {"sheet": source.sheet.sheet_name, "cells": ["H1", "H2"]}
    assert saved.source_reference["equipment_code"] == {"sheet": source.sheet.sheet_name, "cells": ["C1", "C2"]}
    assert [pm.source_reference["pm_name"]["cells"] for pm in saved.software[0].pms] == [["G1"], ["G2"]]
    with storage.transaction() as tx:
        batch = tx.get("import_batch", {"batch_id": receipt.batch_id})
    assert batch["raw_data"] == {source.sheet.sheet_name: source.sheet.cells}


def test_pipeline_preserves_confirmed_alias_raw_text_and_release_id(storage):
    """확정된 KCTC 별칭을 원문 변경 없이 명시 릴리즈 ID로 저장하고 재조회한다.

    param
    - storage: 시연용 KCTC 모델·계열을 등록한 테스트 저장 서비스.
    """
    family_id = _family_id(storage, "Inline O2")
    release = ReleaseService(storage).register_release(ReleaseInput(family_id=family_id, version_raw="v999.3.7", version_order=1))
    release_id = release.release.current["release_version_id"]
    source = review_draft(review_row(group_name="KCTC", model_name="Inline O2", family_name="Inline O2",
                                    equipment_code="QA-PIPE-ALIAS", version_raw="v999.3_7"))
    validator = ImportValidationService(storage)
    with storage.transaction() as tx:
        reference = validator.repository.load(tx, (source,), aliases={family_id: {"v999.3_7": "v999.3.7"}})
    built = build_save_request(source, "pipeline-confirmed-alias", reference)
    assert built.rows[0].family_id == family_id and built.rows[0].release_version_id == release_id
    DatabaseImportSaver(storage).save(built)
    saved = EquipmentService(storage).get_snapshot("QA-PIPE-ALIAS", date(2026, 11, 2))
    assert saved.software[0].version_raw == "v999.3_7" and saved.software[0].release_version_id == release_id
    assert saved.software[0].source_reference["version_raw"] == built.rows[0].source_reference["version_raw"]


def test_mismatched_review_family_or_release_id_cancels_the_batch(storage):
    """검토 후 달라진 계열 ID 또는 다른 계열의 릴리즈 ID를 차단하고 입력 작업까지 취소한다.

    param
    - storage: 연결 오류를 확인할 실제 테스트 저장 서비스.
    """
    o2, o3 = _family_id(storage, "O2"), _family_id(storage, "O3")
    release = ReleaseService(storage).register_release(ReleaseInput(family_id=o3, version_raw="v-other-family-review"))
    other_release_id = release.release.current["release_version_id"]
    source = review_draft(review_row(equipment_code="QA-PIPE-BAD-REVIEW", version_raw="v-review-raw"))
    built = build_save_request(source, "pipeline-invalid-review")
    batches = count(storage, "import_batch")
    for fields, message in (({"family_id": o3}, "계열 ID"),
                            ({"family_id": o2, "release_version_id": other_release_id}, "버전 계열")):
        invalid = replace(built, rows=(replace(built.rows[0], **fields),))
        with pytest.raises(StorageValidationError, match=message):
            DatabaseImportSaver(storage).save(invalid)
        assert count(storage, "import_batch") == batches
    assert EquipmentService(storage).get_snapshot("QA-PIPE-BAD-REVIEW", date(2026, 11, 2)) is None


def test_conflicting_review_release_ids_for_same_software_cancel_all_rows(storage):
    """같은 SW 원문에 서로 다른 검토 릴리즈 ID가 있으면 어느 한 연결을 임의로 선택하지 않는다.

    param
    - storage: 동일 계열의 명시 연결 상충을 확인할 테스트 저장 서비스.
    """
    family_id = _family_id(storage, "O2")
    ids = [ReleaseService(storage).register_release(ReleaseInput(family_id=family_id, version_raw=version))
           .release.current["release_version_id"] for version in ("v-review-link-one", "v-review-link-two")]
    source = review_draft(review_row(equipment_code="QA-PIPE-LINK-CONFLICT", version_raw="v-review-alias"),
                          review_row(2, equipment_code="QA-PIPE-LINK-CONFLICT", pm_name="PM2", version_raw="v-review-alias"))
    built = build_save_request(source, "pipeline-conflicting-review-ids")
    invalid = replace(built, rows=tuple(replace(value, family_id=family_id, release_version_id=release_id)
                                        for value, release_id in zip(built.rows, ids)))
    batches = count(storage, "import_batch")
    with pytest.raises(StorageValidationError, match="릴리즈 연결이 상충"):
        DatabaseImportSaver(storage).save(invalid)
    assert count(storage, "import_batch") == batches
    assert EquipmentService(storage).get_snapshot("QA-PIPE-LINK-CONFLICT", date(2026, 11, 2)) is None


@pytest.mark.parametrize("db_review", [False, True], ids=["기본 검토 요청", "DB 검증 요청"])
def test_c3_mixed_family_names_choose_matching_ui_before_name_priority(storage, db_review):
    """구UI의 정확한 C3 이름이 통합UI의 접두 이름 후보를 가리지 않게 실제 저장 경로를 검증한다.

    param
    - storage: 두 UI 계열을 가진 고유 모델을 등록할 테스트 저장 서비스.
    - db_review: DB 검증의 명시 계열 ID가 있는 요청과 기본 검토 요청 중 확인할 경로.
    """
    model_name = f"QA-C3 혼합 명명 {int(db_review)}"
    code = f"QA-PIPE-C3-MIXED-NAME-{int(db_review)}"
    with storage.transaction() as tx:
        model = tx.insert("equipment_group", {"group_name": model_name, "category": "POLY"})
        tx.insert("version_family", {"group_id": model["group_id"], "family_name": "C3", "ui_type": "LEGACY"})
        integrated = tx.insert("version_family", {"group_id": model["group_id"], "family_name": "C3 통합UI", "ui_type": "INTEGRATED"})
    source = review_draft(review_row(group_name="POLY", model_name=model_name, family_name="C3 통합UI",
                                    equipment_code=code, version_raw="v-c3-mixed-name"))
    request_id = f"pipeline-c3-mixed-name-{db_review}"
    if db_review:
        built, = ImportValidationService(storage).prepare((source,), request_id)
        assert built.rows[0].family_id == integrated["family_id"]
    else:
        built = build_save_request(source, request_id)
        assert built.rows[0].family_id is None
    assert built.rows[0].values["family_name"] == "C3" and built.rows[0].values["ui_type"] == "INTEGRATED"
    DatabaseImportSaver(storage).save(built)
    saved = EquipmentService(storage).get_snapshot(code, date(2026, 11, 2))
    assert saved.software[0].family_id == integrated["family_id"]
    assert saved.software[0].version_raw == "v-c3-mixed-name"


def test_rerelease_is_saved_once_with_first_release_date(storage):
    """같은 버전이 다른 날짜로 두 번 나오면 재배포로 보고 첫 출시일로 한 릴리즈에 Item을 모두 담는다 (멘토 확인 2026-11-05)."""
    rows = [row(1, {"version_raw": "A2", "release_date": "A2", "content": "D2", "item_no": "C2"},
                version_raw="v4.56.9.99", release_date="2026-02-06", item_no=1, content="재배포 블록 Item",
                model_name="M-C4", family_name="C4"),
            row(2, {"version_raw": "A3", "release_date": "A3", "content": "D3", "item_no": "C3"},
                version_raw="v4.56.9.99", release_date="2026-02-02", item_no=1, content="첫 블록 Item",
                model_name="M-C4", family_name="C4")]
    receipt = DatabaseImportSaver(storage).save(request(ExtractKind.RELEASE_NOTE, rows, "req-rerelease"))
    saved = [r for r in ReleaseService(storage).list_releases(_family_id(storage, "C4"))
             if r.current["version_raw"] == "v4.56.9.99"]
    assert len(saved) == 1 and saved[0].current["released_at"] == date(2026, 2, 2)
    assert count(storage, "item", batch_id=receipt.batch_id) == 2
