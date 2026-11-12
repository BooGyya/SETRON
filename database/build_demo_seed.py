"""원본 덤프를 보존하며 시연 SQL의 생산 영역과 빈 계열·HPA 자료를 보완한다.

    .venv/Scripts/python.exe database/build_demo_seed.py

DB에 접속하지 않는다. 원본은 ../docs/04_데이터/원본/SETRON_DB.sql이며,
database/SETRON_DB.sql과 기존 시연 초기화 SQL만 갱신한다. 합성 행은 별도
MANUAL 배치와 manifest로 구분하며 재실행하면 같은 결과를 생성한다.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from io import StringIO
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pymysql.converters import escape_item

from setron.features.db_import.dump import DUMP_COLUMNS, _read_file, columns_from_definition, values_for_columns
from setron.features.reference_data import default_area
from setron.features.storage.validation import TABLE_NAMES, decode_record, encode_record

DOCS = ROOT.parent / "docs"
ORIGINAL = DOCS / "04_데이터" / "원본" / "SETRON_DB.sql"
WORKING = ROOT / "database" / "SETRON_DB.sql"
INITIALIZER = DOCS / "02_시스템_설계" / "데이터베이스" / "SETRON_시연_DB_초기화.sql"
MANIFEST_KEY = "demo_dump.v1:coverage:2026-11-11"
CREATED_AT = "2026-11-11 09:00:00"
RELEASE_SHEET = "시연 합성 릴리즈"
STATUS_SHEET = "시연 합성 HPA 현황"
META_KEY = "[합성]"


@dataclass
class Dump:
    prefix: str
    header: str
    definitions: dict[str, str]
    footer: str
    rows: dict[str, list[dict]]


def read_sql(sql: str) -> Dump:
    """앱의 엄격한 덤프 파서를 사용하고 초기화용 CREATE DATABASE 부분은 보존한다."""
    prefix, marker, body = sql.partition("-- SETRON DB 내보내기\n")
    if not marker:
        raise ValueError("SETRON 덤프의 시작을 찾을 수 없습니다.")
    body = marker + body
    decoded = _read_file(StringIO(body))
    matches = list(re.finditer(r"CREATE TABLE `(\w+)` \(.*?\n\) ENGINE=.*?;\n", body, re.S))
    if [m[1] for m in matches] != list(TABLE_NAMES):
        raise ValueError("시연 덤프의 테이블 정의가 올바르지 않습니다.")
    first = body.index("-- 테이블 import_batch")
    end = body.rindex("\nSET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS;")
    return Dump(prefix, body[:first], {m[1]: m[0] for m in matches}, body[end:],
                {table: [decode_record(table, row) for row in rows] for table, rows in decoded.items()})


def _remove_generated(rows):
    manifests = [r for r in rows["app_setting"] if r["setting_key"] == MANIFEST_KEY]
    if len(manifests) > 1:
        raise ValueError("합성 자료 manifest가 중복되었습니다.")
    if not manifests:
        return
    for table, keys in manifests[0]["setting_value"]["owned_rows"].items():
        fields = ("release_version_id", "item_id") if table == "release_version_item" else (DUMP_COLUMNS[table][0],)
        owned = {tuple(key) for key in keys}
        rows[table] = [r for r in rows[table] if tuple(r[field] for field in fields) not in owned]
    rows["app_setting"] = [r for r in rows["app_setting"] if r["setting_key"] != MANIFEST_KEY]


def _raw_original(raw, source):
    if not source:
        return None
    refs = source if isinstance(source, list) else [source]
    values = {raw.get(ref["sheet"], {}).get(cell) for ref in refs for cell in ref["cells"]}
    values.discard(None)
    return next(iter(values)) if len(values) == 1 else None


def augment(original: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """기존 릴리즈·설정·위험 지정은 유지하고 이 생성기가 소유한 행만 다시 만든다."""
    rows = deepcopy(original)
    _remove_generated(rows)
    batches = {r["batch_id"]: r["raw_data"] for r in rows["import_batch"]}
    area_counts = {}
    model_restored = 0
    for snapshot in rows["equipment_snapshot"]:
        area = default_area(snapshot["site"], snapshot["line"])
        if snapshot["production_area"] is None and area is not None:
            snapshot["production_area"] = area
        area_counts[snapshot["production_area"]] = area_counts.get(snapshot["production_area"], 0) + 1
        raw = _raw_original(batches[snapshot["batch_id"]], (snapshot["source_reference"] or {}).get("model_name"))
        if raw is not None and raw != snapshot["model_name"]:
            snapshot["model_name"] = raw
        # 재생성해도 보완 대상 건수가 같도록 등록명과 원문의 차이를 센다.
        model = next((r["group_name"] for r in rows["equipment_group"] if r["group_id"] == snapshot["group_id"]), None)
        model_restored += raw is not None and raw != model

    owned = {}

    def add(table, values):
        fields = ("release_version_id", "item_id") if table == "release_version_item" else (DUMP_COLUMNS[table][0],)
        key = [values[field] for field in fields]
        if any(all(row[field] == values[field] for field in fields) for row in rows[table]):
            raise ValueError(f"합성 {table} 키가 기존 자료와 겹칩니다: {key}")
        rows[table].append(values)
        owned.setdefault(table, []).append(key)

    def next_id(table):
        return max((r[DUMP_COLUMNS[table][0]] for r in rows[table]), default=0) + 1

    def ref(sheet, **fields):
        return {name: {"sheet": sheet, "cells": [cell]} for name, cell in fields.items()}

    before = {table: len(values) for table, values in rows.items()}
    batch_id = next_id("import_batch")
    raw = {"[파일]": "SETRON 시연 합성 릴리즈 (database/build_demo_seed.py 생성 자료)",
           META_KEY: {"synthetic": True, "generator": "database/build_demo_seed.py", "revision": 1,
                      "description": "빈 계열·HPA 시연용 합성 자료. 실제 설비·실제 릴리즈 정보가 아님."},
           RELEASE_SHEET: {"A1": "계열", "B1": "버전", "C1": "출시일", "D1": "순서", "E1": "Item", "F1": "Item 번호"}}
    add("import_batch", {"batch_id": batch_id, "source_type": "MANUAL", "imported_at": CREATED_AT, "raw_data": raw})
    missing = [f for f in rows["version_family"] if not any(r["family_id"] == f["family_id"] for r in rows["release_version"])]
    hpa = next(r for r in rows["equipment_group"] if r["group_name"] == "HPA")
    hpa_family = next(f for f in rows["version_family"] if f["group_id"] == hpa["group_id"])
    for family in missing:
        note_id = next_id("release_note")
        add("release_note", {"release_note_id": note_id, "group_id": family["group_id"], "release_date": "2026-04-06", "batch_id": batch_id})
        for number in range(1, 4):
            line = len(raw[RELEASE_SHEET]) // 6 + 1
            version = f"v0.0.{family['family_id']}.{number}"
            released_at = f"2026-{number + 3:02}-06"
            content = f"시연 합성: {family['family_name']} {version}의 기능 확인용 Item {number}"
            raw[RELEASE_SHEET].update({f"A{line}": family["family_name"], f"B{line}": version,
                                       f"C{line}": released_at, f"D{line}": number * 10,
                                       f"E{line}": content, f"F{line}": number})
            release_id, item_id = next_id("release_version"), next_id("item")
            add("release_version", {"release_version_id": release_id, "release_note_id": note_id,
                "family_id": family["family_id"], "version_raw": version, "version_suffix": None,
                "released_at": released_at, "version_order": number * 10, "item_mapping_status": "VERIFIED",
                "batch_id": batch_id, "source_reference": ref(RELEASE_SHEET, version_raw=f"B{line}", released_at=f"C{line}", version_order=f"D{line}"), "edited_fields": []})
            add("item", {"item_id": item_id, "release_note_id": note_id, "item_no": number, "content": content,
                "batch_id": batch_id, "source_reference": ref(RELEASE_SHEET, item_no=f"F{line}", content=f"E{line}"), "edited_fields": []})
            add("release_version_item", {"release_version_id": release_id, "item_id": item_id,
                "item_state": "APPLIED", "target_scope": "LISTED", "target_group_ids": [family["group_id"]]})

    hpa_releases = sorted((r for r in rows["release_version"] if r["family_id"] == hpa_family["family_id"]), key=lambda r: r["version_order"])
    dates = sorted({r["base_date"] for r in rows["equipment_snapshot"]})
    # 현황 편집 화면은 배치당 하나의 원본 시트를 열므로 릴리즈와 현황의 배치를 나눈다.
    status_batch_id = next_id("import_batch")
    status_raw = {"[파일]": "SETRON 시연 합성 HPA 현황 (database/build_demo_seed.py 생성 자료)",
                  META_KEY: deepcopy(raw[META_KEY]),
                  STATUS_SHEET: {"A1": "설비호기", "B1": "사업장", "C1": "라인", "D1": "모델", "E1": "생산 영역", "F1": "기준일", "G1": "SW 버전"}}
    add("import_batch", {"batch_id": status_batch_id, "source_type": "MANUAL", "imported_at": CREATED_AT, "raw_data": status_raw})
    for code, site, line in (("DEMO-HPA-M01", "화성", "13L"), ("DEMO-HPA-M02", "화성", "15L"), ("DEMO-HPA-F01", "화성", "1L (기흥)")):
        equipment_id = next_id("equipment")
        add("equipment", {"equipment_id": equipment_id, "equipment_code": code})
        for index, day in enumerate(dates):
            release = hpa_releases[min(index, len(hpa_releases) - 1)]
            cell_row = len(status_raw[STATUS_SHEET]) // 7 + 1
            area = default_area(site, line)
            status_raw[STATUS_SHEET].update({f"A{cell_row}": code, f"B{cell_row}": site, f"C{cell_row}": line,
                f"D{cell_row}": "HPA", f"E{cell_row}": area, f"F{cell_row}": day, f"G{cell_row}": release["version_raw"]})
            snapshot_id = next_id("equipment_snapshot")
            add("equipment_snapshot", {"snapshot_id": snapshot_id, "equipment_id": equipment_id,
                "base_date": day, "site": site, "line": line, "model_name": "HPA", "group_id": hpa["group_id"],
                "production_area": area, "structure_type": None, "geometry_type": None, "modification_type": None,
                "batch_id": status_batch_id, "source_reference": ref(STATUS_SHEET, equipment_code=f"A{cell_row}", site=f"B{cell_row}", line=f"C{cell_row}", model_name=f"D{cell_row}", production_area=f"E{cell_row}", base_date=f"F{cell_row}")})
            add("equipment_version", {"equipment_version_id": next_id("equipment_version"), "snapshot_id": snapshot_id,
                "family_id": hpa_family["family_id"], "version_raw": release["version_raw"], "release_version_id": release["release_version_id"],
                "batch_id": status_batch_id, "source_reference": ref(STATUS_SHEET, version_raw=f"G{cell_row}")})

    rows["app_setting"].append({"setting_key": MANIFEST_KEY, "setting_value": {
        "schema": 1, "synthetic": True, "generator_revision": 1, "owned_rows": owned,
        "before": before, "original_area_counts": area_counts, "restored_model_names": model_restored,
        "area_basis": "reference_data.default_area(site,line): 멘토 확정 장소표. 원본 Excel 셀 값은 변경하지 않음.",
        "model_name_basis": "기존 batch_id/source_reference가 가리키는 원본 모델명 복원",
        "synthetic_families": [f["family_id"] for f in missing], "hpa_equipment": 3}})
    return rows


def render(dump: Dump, rows) -> str:
    """앱에서 다시 읽을 수 있는 동일 SQL 형식으로 렌더링한다."""
    total = sum(map(len, rows.values()))
    header = re.sub(r"(-- 테이블 13개 · )\d+행", rf"\g<1>{total}행", dump.header)
    out = [dump.prefix, header]
    for table in TABLE_NAMES:
        definition = dump.definitions[table]
        if "AUTO_INCREMENT=" in definition:
            highest = max((r[DUMP_COLUMNS[table][0]] for r in rows[table]), default=0)
            definition = re.sub(r"AUTO_INCREMENT=\d+", f"AUTO_INCREMENT={highest + 1}", definition)
        out.append(f"-- 테이블 {table} · {len(rows[table])}행\n{definition}")
        if rows[table]:
            columns = columns_from_definition(table, definition)
            out.append(f"INSERT INTO `{table}` (" + ", ".join(f"`{c}`" for c in columns) + ") VALUES\n")
            encoded = [encode_record(table, row) for row in rows[table]]

            def literal(value):
                return "_binary X'" + value.hex() + "'" if isinstance(value, bytes) else escape_item(value, "utf8mb4")

            out.append(",\n".join("(" + ", ".join(literal(value) for value in values_for_columns(table, row, columns)) + ")"
                                 for row in encoded) + ";\n")
        out.append("\n")
    out.append(dump.footer.lstrip("\n"))
    result = "".join(out)
    # 행 수·SQL 리터럴·JSON·BINARY를 실제 앱 파서로 다시 검사한다.
    read_sql(result)
    return result


def build(sql: str) -> str:
    dump = read_sql(sql)
    return render(dump, augment(dump.rows))


def main():
    # 두 입력의 검증을 모두 끝낸 뒤 지정한 출력만 갱신한다.
    outputs = [(WORKING, build(ORIGINAL.read_text(encoding="utf-8"))),
               (INITIALIZER, build(INITIALIZER.read_text(encoding="utf-8")))]
    for path, sql in outputs:
        path.write_text(sql, encoding="utf-8", newline="\n")
        print(f"{path.name}: {len(read_sql(sql).rows['equipment'])}대 / {len(read_sql(sql).rows['equipment_snapshot'])}개 관측")


if __name__ == "__main__":
    main()
