"""SQL 병합이 원본과 설정의 참조를 다른 행에 연결하지 않는지 검증한다."""

import json

import pytest

from setron.features.db_import.references import ReferenceConflict, rewrite_json


def _resolver(**tables):
    def resolve(table, value):
        return tables[table][value]
    return resolve


def test_managed_model_aliases_follow_remapped_group_ids():
    row = {"setting_key": b"hierarchy.model_aliases.v1", "setting_value": '{"aliases":{"old":7}}'}
    result = rewrite_json("app_setting", row, _resolver(equipment_group={7: 107}), ids_changed=True)
    assert json.loads(result["setting_value"])["aliases"] == {"old": 107}
    assert json.loads(row["setting_value"])["aliases"] == {"old": 7}


def test_rewrites_active_references_and_rejects_missing_sources():
    row = {"source_reference": '{"line":[{"sheet":"S","cells":["A2"],"batch_id":5}]}'}
    resolve = _resolver(import_batch={5: 105}, equipment_group={7: 107})

    result = rewrite_json("equipment_snapshot", row, resolve, ids_changed=True)
    assert json.loads(result["source_reference"])["line"][0]["batch_id"] == 105
    assert json.loads(row["source_reference"])["line"][0]["batch_id"] == 5
    item = rewrite_json("release_version_item", {"target_group_ids": "[7]"}, resolve, ids_changed=True)
    assert item["target_group_ids"] == "[107]"

    with pytest.raises(ReferenceConflict, match="원본·설정 참조"):
        rewrite_json("equipment_snapshot", row, _resolver(import_batch={}), ids_changed=False)


def test_risk_scope_maps_live_model_and_retains_deleted_release_as_evidence():
    source = {"family": {"family_id": 2}, "item_manager": {"model_ids": [4]},
              "start_version": {"family_id": 2, "release_version_id": 9, "version_raw": "v9"}}
    resolve = _resolver(version_family={2: 22}, equipment_group={4: 44}, release_version={})
    result = rewrite_json("risk_rule", {"target_reference": json.dumps(source)}, resolve, ids_changed=True)

    payload = json.loads(result["target_reference"])
    assert payload["family"]["family_id"] == 22
    assert payload["item_manager"]["model_ids"] == [44]
    assert payload["start_version"] == {"family_id": 22, "release_version_id": "원본 ID 9", "version_raw": "v9"}


def test_manual_journal_keeps_historical_rows_and_rewrites_present_sources():
    raw = {"management_edit": {
        "before": [{"current": {"pm_id": 5}, "software": {"equipment_version_id": 6, "batch_id": 1,
                    "source_reference": {"version_raw": {"sheet": "S", "cells": ["A1"]}}}}],
        "changes": [{"key": {"pm_id": 5}, "changes": {"group_id": 4}}],
    }}
    resolve = _resolver(equipment_pm={5: 55}, equipment_version={}, equipment_group={4: 44}, import_batch={1: 11})
    row = {"source_type": "MANUAL", "raw_data": json.dumps(raw)}

    edited = rewrite_json("import_batch", row, resolve, ids_changed=True)

    journal = json.loads(edited["raw_data"])["management_edit"]
    assert journal["before"][0]["current"]["pm_id"] == journal["changes"][0]["key"]["pm_id"] == 55
    assert journal["changes"][0]["changes"]["group_id"] == 44
    assert journal["before"][0]["software"]["equipment_version_id"] == "원본 ID 6"
    assert journal["before"][0]["software"]["batch_id"] == 11
    assert rewrite_json("import_batch", {**row, "source_type": "EXCEL"}, resolve,
                        ids_changed=True)["raw_data"] == row["raw_data"]


def test_group_history_remaps_key_and_prevents_deleted_release_from_becoming_active():
    key = b"version_groups.history.v2:1:2:00000000000000000001"
    row = {"setting_key": key, "setting_value": '{"groups":{"N":[3,4]}}'}
    resolve = _resolver(equipment_group={1: 11}, version_family={2: 22}, release_version={3: 33})

    result = rewrite_json("app_setting", row, resolve, ids_changed=True)

    assert result["setting_key"] == b"version_groups.history.v2:11:22:00000000000000000001"
    assert json.loads(result["setting_value"]) == {"groups": {"N": [33]}, "source_unmapped_groups": {"N": [4]}}


def test_applicability_remaps_candidates_and_requires_fresh_confirmation():
    source = {"model_id": 1, "family_id": 2, "pair_fingerprint": "source-pair",
              "candidates": [{"confirmation": {"release_id": 3}, "fingerprint": "source-release"}]}
    row = {"setting_key": b"target_applicability.v1:1:2", "setting_value": json.dumps(source)}
    resolve = _resolver(equipment_group={1: 11}, version_family={2: 22}, release_version={3: 33})

    result = rewrite_json("app_setting", row, resolve, ids_changed=True)

    assert result["setting_key"] == b"target_applicability.v1:11:22"
    saved = json.loads(result["setting_value"])
    assert (saved["model_id"], saved["family_id"], saved["pair_fingerprint"]) == (11, 22, "")
    assert saved["candidates"][0]["confirmation"]["release_id"] == 33
    identity = lambda _table, value: value
    assert rewrite_json("app_setting", row, identity, ids_changed=False) == row


def test_deleted_snapshot_keeps_internal_restore_links_separate_from_live_ids():
    raw = {"management_delete": {"batch_id": 1, "rows": [{"snapshot_id": 3, "data": {
        "snapshot": {"snapshot_id": 3, "equipment_id": 2, "group_id": 4, "batch_id": 1},
        "versions": [{"equipment_version_id": 6, "snapshot_id": 3, "family_id": 7, "batch_id": 1,
                      "source_reference": {"version_raw": {"sheet": "S", "cells": ["A1"], "batch_id": 1}}}],
        "pms": [{"pm_id": 8, "snapshot_id": 3, "equipment_version_id": 6, "group_id": 4, "batch_id": 1}],
    }}]}}
    resolve = _resolver(import_batch={1: 101}, equipment={2: 102}, equipment_group={4: 104},
                        version_family={7: 107}, equipment_snapshot={}, equipment_version={}, equipment_pm={})
    original = json.dumps(raw)
    result = rewrite_json("import_batch", {"source_type": "MANUAL", "raw_data": original}, resolve, ids_changed=True)
    deleted = json.loads(result["raw_data"])["management_delete"]
    data = deleted["rows"][0]["data"]

    assert deleted["batch_id"] == data["snapshot"]["batch_id"] == 101
    assert data["snapshot"]["equipment_id"] == 102 and data["snapshot"]["group_id"] == 104
    assert deleted["rows"][0]["snapshot_id"] == data["snapshot"]["snapshot_id"] == "원본 ID 3"
    assert data["versions"][0]["equipment_version_id"] == data["pms"][0]["equipment_version_id"] == "원본 ID 6"
    assert data["versions"][0]["family_id"] == 107 and data["pms"][0]["pm_id"] == "원본 ID 8"
    assert data["versions"][0]["source_reference"]["version_raw"]["batch_id"] == 101
    assert json.dumps(raw) == original


def test_restore_and_risk_delete_journals_remap_their_source_and_parent_journal():
    raw = {"management_restore": {"batch_id": 2, "source": 1},
           "management_restore_release": {"batch_id": 3, "release_version_id": 4},
           "management_reorder": {"release_version_id": 4},
           "management_delete_risk": {"key": {"risk_rule_id": 5},
                                      "before": {"current": {"risk_rule_id": 5, "family_id": 6}}}}
    resolve = _resolver(import_batch={1: 101, 2: 102, 3: 103}, release_version={4: 104},
                        risk_rule={}, version_family={6: 106})
    result = rewrite_json("import_batch", {"source_type": "MANUAL", "raw_data": json.dumps(raw)}, resolve,
                          ids_changed=True)
    journal = json.loads(result["raw_data"])

    assert journal["management_restore"] == {"batch_id": 102, "source": 101}
    assert journal["management_restore_release"] == {"batch_id": 103, "release_version_id": 104}
    assert journal["management_reorder"]["release_version_id"] == 104
    assert journal["management_delete_risk"]["key"]["risk_rule_id"] == "원본 ID 5"
    assert journal["management_delete_risk"]["before"]["current"]["family_id"] == 106


def test_release_delete_journal_remaps_kept_items_and_archives_deleted_ids():
    raw = {"management_delete_release": {"release_version_id": 1, "family_id": 2, "item_id": None,
        "release": {"release_version_id": 1, "family_id": 2, "release_note_id": 3, "batch_id": 4},
        "items": [
            {"item_id": 5, "kept": True, "item": {"item_id": 5, "batch_id": 4},
             "link": {"release_version_id": 1, "item_id": 5, "target_group_ids": [6]}},
            {"item_id": 7, "kept": False, "item": {"item_id": 7, "batch_id": 4},
             "link": {"release_version_id": 1, "item_id": 7, "target_group_ids": None}},
        ]}}
    resolve = _resolver(release_version={}, version_family={2: 102}, release_note={3: 103},
                        import_batch={4: 104}, item={5: 105}, equipment_group={6: 106})
    result = rewrite_json("import_batch", {"source_type": "MANUAL", "raw_data": json.dumps(raw)}, resolve,
                          ids_changed=True)
    deleted = json.loads(result["raw_data"])["management_delete_release"]

    assert deleted["release_version_id"] == deleted["release"]["release_version_id"] == "원본 ID 1"
    assert deleted["family_id"] == deleted["release"]["family_id"] == 102
    assert (deleted["release"]["release_note_id"], deleted["release"]["batch_id"]) == (103, 104)
    kept, removed = deleted["items"]
    assert kept["item_id"] == kept["item"]["item_id"] == kept["link"]["item_id"] == 105
    assert kept["link"]["target_group_ids"] == [106]
    assert removed["item_id"] == removed["item"]["item_id"] == removed["link"]["item_id"] == "원본 ID 7"
