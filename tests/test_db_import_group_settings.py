"""SQL 병합에서 N 그룹 기준과 이력의 순번을 함께 처리한다."""

import json

import pytest

from setron.features.db_import.group_settings import group_history_prune


def _rows(count, *, model=1, schema=3):
    entries = [{"saved_at": "2026-11-11T00:00:00", "memo": "", "groups": {"N": [i]}}
               for i in range(1, count + 1)]
    current = {"schema": schema, "model_id": model, "family_id": 2}
    if schema == 1:
        current["history"] = entries
        entries = []
    else:
        current.update(sequence=count, current=entries[-1])
        if schema == 2:
            entries = entries[:-1]
        else:
            current["active_sequence"] = count
    return [
        {"setting_key": f"version_groups.v1:{model}:2".encode(), "setting_value": json.dumps(current)},
        *({"setting_key": f"version_groups.history.v2:{model}:2:{number:020d}".encode(),
           "setting_value": json.dumps(entry)} for number, entry in enumerate(entries, 1)),
    ]


def test_add_retains_current_group_and_suppresses_only_new_history_for_it():
    incoming = _rows(3)
    assert group_history_prune([*incoming, *_rows(1, model=7)], _rows(1), "add") == (
        {incoming[2]["setting_key"], incoming[3]["setting_key"]}, set(),
    )


@pytest.mark.parametrize("schema, history_count", [(1, 0), (2, 1), (3, 2)])
def test_overwrite_prunes_only_obsolete_history_for_current_groups(schema, history_count):
    target = _rows(3)
    assert group_history_prune(_rows(2, schema=schema), [*target, *_rows(2, model=7)], "overwrite") == (
        set(), {row["setting_key"] for row in target[history_count + 1:]},
    )


def test_missing_current_does_not_prune_target_and_incomplete_bundle_is_rejected():
    incoming = _rows(2)
    assert group_history_prune(incoming[1:], _rows(3), "overwrite") == (
        {row["setting_key"] for row in incoming[1:]}, set(),
    )
    with pytest.raises(ValueError, match="N 그룹 설정과 저장 이력"):
        group_history_prune(incoming[:-1], _rows(3), "overwrite")


def test_existing_orphan_history_does_not_get_reassigned_to_new_current_group():
    incoming = _rows(1)
    assert group_history_prune(incoming, _rows(2)[1:], "overwrite") == (
        {row["setting_key"] for row in incoming}, set(),
    )
