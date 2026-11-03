"""릴리즈노트 나열 순서로 버전 순서 후보를 만드는 규칙을 검증한다 (데이터 파트 release_parser 규칙 연동)."""

from datetime import date

from setron.features.release.order_candidates import suggest_version_orders


def release(rid, raw, cell, *, batch=5, sheet="Release List", order=None, released=None, family=1):
    return {"release_version_id": rid, "family_id": family, "version_raw": raw, "version_order": order,
            "released_at": released, "batch_id": batch,
            "source_reference": {"version_raw": {"sheet": sheet, "cells": cell if isinstance(cell, list) else [cell]}}}


def test_orders_follow_listing_rows_in_steps_of_ten():
    rows = [release(3, "v3", "A30"), release(1, "v1", ["A4", "A5"]), release(2, "v2", "A12"),
            release(9, "o2", "A2", family=2)]

    result = suggest_version_orders(1, rows)

    assert result.reason is None
    assert [(c.version_raw, c.version_order, c.row) for c in result.candidates] == [
        ("v1", 10, 4), ("v2", 20, 12), ("v3", 30, 30)]


def test_date_going_backwards_is_warned_but_still_suggested():
    rows = [release(1, "v1", "A2", released=date(2026, 3, 1)), release(2, "v2", "A9", released=date(2026, 1, 8))]

    result = suggest_version_orders(1, rows)

    assert len(result.candidates) == 2
    assert result.warnings and "v2" in result.warnings[0]


def test_no_candidates_when_order_exists_or_sources_are_missing_or_mixed():
    assert "이미 순서" in suggest_version_orders(1, [release(1, "v1", "A2", order=1), release(2, "v2", "A3")]).reason
    missing = release(2, "v2", "A3")
    missing["source_reference"] = None
    assert "원본 버전 셀" in suggest_version_orders(1, [release(1, "v1", "A2"), missing]).reason
    mixed = [release(1, "v1", "A2"), release(2, "v2", "A3", batch=6)]
    assert "여러 입력 작업" in suggest_version_orders(1, mixed).reason
    assert "없습니다" in suggest_version_orders(7, []).reason
