"""공백까지 구분하는 원본 릴리즈가 실제 가져오기 검증·저장을 통과한다."""

from dataclasses import replace

from setron.features.import_save.service import DatabaseImportSaver, _validation_draft
from setron.features.release.replacement_schemas import ReplacementReview
from test_release_import_integration import import_request
from test_release_replacement import original
from test_release_revision import all_rows, observation, revision_storage
from setron.ui.database_source import load_dashboard_source
from setron.ui.version_data import catalog_from_dashboard
from setron.ui.item_data import item_data_from_source


def test_actual_import_preserves_distinct_raw_versions(revision_storage):
    """원문이 v1과 v1 공백인 두 릴리즈를 임의 병합하거나 중복으로 차단하지 않는다."""
    storage = revision_storage
    first = original(storage)
    second = original(storage, raw="v1 ", number=8, order=2)
    ids = {first.release.current["release_version_id"], second.release.current["release_version_id"]}
    saver = DatabaseImportSaver(storage)
    one = import_request(storage, identity="exact-one", sheet="첫시트")
    two = import_request(storage, identity="exact-two", raw="v1 ", sheet="둘째시트")
    two = replace(two, rows=(replace(two.rows[0], values={**two.rows[0].values, "item_no": 8}),),
                  raw_data={"sheet": "둘째시트", "cells": {**two.raw_data["cells"], "C1": 8}})
    # 기존값 조회도 동일한 원문 두 개를 정확히 구분해야 교체 검토까지 도달할 수 있다.
    reference = saver.load_reference((_validation_draft(one), _validation_draft(two)))
    catalog = next(f.releases for f in reference.families if f.releases.family_id == 1)
    assert catalog.match("v1").release_version_id != catalog.match("v1 ").release_version_id
    assert catalog.match(" v1") is None
    plan, = saver.preview_release_import((one, two))
    review = ReplacementReview(fingerprint=plan.preview.fingerprint,
        choices={(c.kind, c.key, c.field): "INCOMING" for c in plan.preview.conflicts},
        link_choices={(c.release_key, c.item_key): "CURRENT" for c in plan.preview.link_conflicts})
    saver.save_many(tuple(replace(request, release_reviews=(review,)) for request in (one, two)))
    rows = all_rows(storage, "release_version")
    assert {row["version_raw"] for row in rows} == {"v1", "v1 "}
    assert {row["release_version_id"] for row in rows} == ids
    observation(storage, versions=((1, 1, "v1", first.release.current["release_version_id"]),))
    source = load_dashboard_source(storage)
    displayed = catalog_from_dashboard(source)
    assert {entry.release.version_raw for entry in displayed.list_releases(1, 1)} == {"v1", "v1 "}
    item_data = item_data_from_source(displayed, source)
    assert len(item_data.versions.releases) == 2
    assert item_data.catalog().families[1].match("v1 ").release_version_id in ids
