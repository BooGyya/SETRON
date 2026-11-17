"""원본 기반 정답 생성, GMS 증거 수집, 전체 필드 대조. 제품 정규화로 정답을 만들지 않는다."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import date, datetime
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FIELDS = (
    "base_date", "site", "line", "group_name", "model_name", "production_area",
    "equipment_code", "pm_name", "family_name", "version_raw", "ui_type",
    "structure_type", "geometry_type", "modification_type", "engine_version", "epd_version",
)
LAYOUTS = {
    "Memory": {"model": "D", "code": "E", "sw": "H", "engine": "I", "epd": "K", "pm": None},
    "Foundry": {"model": "C", "code": "D", "sw": "G", "engine": "H", "epd": "J", "pm": "P"},
}
MODEL_GROUPS = {
    "M-C3": "POLY", "C3": "POLY", "M-C4": "POLY", "M-C4+": "POLY", "M-C4/C4+": "POLY",
    "M-O2": "OXIDE", "M-O3": "OXIDE", "M-O5": "OXIDE", "M-O5 Prime": "OXIDE",
    "Inline O2": "KCTC", "Cluster O2": "KCTC", "HPA": "HPA",
}


def now():
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat()


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def encode(value):
    if isinstance(value, datetime):
        return {"excel_type": "datetime", "iso": value.isoformat()}
    if isinstance(value, date):
        return {"excel_type": "date", "iso": value.isoformat()}
    raise TypeError(type(value).__name__)


def decode(value):
    if isinstance(value, dict) and value.get("excel_type") in {"date", "datetime"}:
        return (datetime if value["excel_type"] == "datetime" else date).fromisoformat(value["iso"])
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items()}
    return value


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode) + "\n", encoding="utf-8")


def excel_address(row, col):
    letters = ""
    while col:
        col, remainder = divmod(col - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters + str(row)


def coordinates(address):
    match = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})", str(address).strip())
    if not match:
        return None
    col = 0
    for letter in match[1].upper():
        col = col * 26 + ord(letter) - 64
    return int(match[2]), col


def cells_of(sheet):
    return {excel_address(sheet["first_row"] + i, sheet["first_col"] + j): value
            for i, row in enumerate(sheet["values"]) for j, value in enumerate(row)}


def anchor(sheet, address):
    position = coordinates(address)
    if position is None:
        return None
    row, col = position
    for merged in sheet["merged_ranges"]:
        if merged["top"] <= row <= merged["bottom"] and merged["left"] <= col <= merged["right"]:
            return excel_address(merged["top"], merged["left"])
    return excel_address(row, col)


def source_cell(sheet, column, row):
    address = anchor(sheet, column + str(row))
    value = cells_of(sheet).get(address)
    return (None if value in (None, "") else str(value)), address


def fact(value=None, sources=(), rule="source_absent", state=None):
    return {"value": value, "state": state or ("source_missing" if value is None else "explicit"),
            "sources": list(sources), "rule": rule}


def model_parts(label):
    """관찰한 원본 표기를 규칙으로 분리한다. 제품의 normalize()를 호출하지 않는다."""
    ui = next((name for name in ("구UI", "통합UI", "Smart UI") if name in label), None)
    geometry = re.findall(r"[56]각", label)
    if len(set(geometry)) > 1:
        raise ValueError("Ambiguous geometry: " + label)
    modification = "CIP" if "CIP" in label else "Prime" if "Prime" in label else None
    structure = "Inline" if label.startswith("Inline ") else "Cluster" if label.startswith("Cluster ") else None
    if label.startswith("M-C3"):
        model = "M-C3"
    elif label.startswith("M-C4"):
        model = label.split(" / Smart UI")[0].strip()
    elif label.startswith("M-O5(Prime)"):
        model = "M-O5(Prime)"
    elif label.startswith(("Inline ", "Cluster ")):
        model = re.sub(r"\s*\(CIP\)$", "", label).strip()
    elif label in {"M5", "M-O2", "M-O3", "M-O5", "M-O5 Prime", "HPA"}:
        model = label
    else:
        raise ValueError("Unreviewed source model: " + label)
    canonical = {"M5": "M-O5", "M-O5(Prime)": "M-O5 Prime"}.get(model, model)
    return model, canonical, ui, geometry[0] if geometry else None, modification, structure


def build_reference(snapshot):
    records = []
    for sheet in snapshot["sheets"]:
        name = sheet["sheet_name"]
        layout = LAYOUTS[name]
        cells = cells_of(sheet)
        for row in range(4, sheet["first_row"] + len(sheet["values"])):
            code, code_address = source_cell(sheet, layout["code"], row)
            if code is None:
                continue
            label, model_address = source_cell(sheet, layout["model"], row)
            if label is None:
                raise ValueError("Model missing: " + name + ":" + str(row))
            line, line_address = source_cell(sheet, "B", row)
            site = None
            if line:
                location = re.fullmatch(r"(.+?)\s*\((기흥|화성|평택)\)", line, re.DOTALL)
                if location:
                    line, site = location[1].strip(), location[2]
            group, group_address = source_cell(sheet, "A", row)
            version, version_address = source_cell(sheet, layout["sw"], row)
            engine, engine_address = source_cell(sheet, layout["engine"], row)
            epd, epd_address = source_cell(sheet, layout["epd"], row)
            alternatives = []
            if label == "M-O2/O3":
                description, pm_address = source_cell(sheet, layout["pm"], row)
                groups = re.findall(r"PM([0-9]+(?:\s*,\s*[0-9]+)*)는\s*(O[235])", description or "")
                versions = dict(re.findall(r"(O[235]):\s*([^/]+)", version or ""))
                if len(groups) != 2 or set(versions) != {"O2", "O3"}:
                    raise ValueError("Unreviewed mixed arrangement.")
                for numbers, family in groups:
                    for i, number in enumerate(re.findall(r"[0-9]+", numbers)):
                        raw_model = "M-O2" if family == "O2" else "O3"
                        alternatives.append({"raw_model": raw_model, "model": "M-" + family,
                            "pm": "PM" + number, "raw_pm": ("PM" if i == 0 else "") + number,
                            "pm_source": pm_address, "version": versions[family].strip(),
                            "ui": None, "geometry": None, "modification": None, "structure": None})
            else:
                raw_model, model, ui, geometry, modification, structure = model_parts(label)
                alternatives.append({"raw_model": raw_model, "model": model, "pm": None, "raw_pm": None,
                    "pm_source": None, "version": version, "ui": ui, "geometry": geometry,
                    "modification": modification, "structure": structure})
            for item in alternatives:
                raw = {field: fact() for field in FIELDS}
                raw.update({
                    "site": fact(site, [line_address] if site else [], "explicit_site_in_line"),
                    "line": fact(line, [line_address], "actual_merge_only"),
                    "group_name": fact(group, [group_address], "source_group_label"),
                    "model_name": fact(item["raw_model"], [model_address], "split_model_annotations"),
                    "equipment_code": fact(code, [code_address], "equipment_column_exact"),
                    "pm_name": fact(item["raw_pm"], [item["pm_source"]] if item["pm_source"] else [], "explicit_pm_list"),
                    "version_raw": fact(item["version"], [version_address], "sw_column_or_labeled_component"),
                    "ui_type": fact(item["ui"], [model_address] if item["ui"] else [], "explicit_ui_annotation"),
                    "geometry_type": fact(item["geometry"], [model_address] if item["geometry"] else [], "explicit_geometry_annotation"),
                    "modification_type": fact(item["modification"], [model_address] if item["modification"] else [], "explicit_modification_annotation"),
                    "structure_type": fact(item["structure"], [model_address] if item["structure"] else [], "explicit_structure_annotation"),
                    "engine_version": fact(engine, [engine_address], "engine_column_only"),
                    "epd_version": fact(epd, [epd_address], "epd_column_only"),
                })
                normalized = json.loads(json.dumps(raw, ensure_ascii=False))
                normalized["model_name"] = fact(item["model"], [model_address], "confirmed_model_normalization", "derived")
                if item["pm"]:
                    normalized["pm_name"] = fact(item["pm"], [item["pm_source"]], "explicit_pm_position", "derived")
                if item["structure"]:
                    normalized["structure_type"] = fact(item["structure"].upper(), [model_address], "structure_uppercase", "derived")
                if item["ui"] in {"구UI", "통합UI"}:
                    normalized["ui_type"] = fact({"구UI": "LEGACY", "통합UI": "INTEGRATED"}[item["ui"]], [model_address], "confirmed_c3_ui_mapping", "derived")
                elif item["ui"]:
                    normalized["ui_type"] = fact(None, [model_address], "no_confirmed_smart_ui_mapping", "unresolved")
                if item["model"] in {"M-C3", "C3"}:
                    normalized["family_name"] = fact("C3", [model_address], "confirmed_c3_shared_family", "derived")
                else:
                    normalized["family_name"] = fact(None, [model_address], "no_confirmed_family_catalog_in_source", "unresolved")
                category = group.upper() if group else MODEL_GROUPS.get(item["model"])
                expected_group = MODEL_GROUPS.get(item["model"])
                evidence = [address for address in (group_address if group else None, model_address) if address]
                if category not in {None, "POLY", "OXIDE", "KCTC", "HPA"} or (category and expected_group and category != expected_group):
                    normalized["group_name"] = fact(None, evidence, "source_group_model_conflict", "unresolved")
                else:
                    normalized["group_name"] = fact(category, evidence if category else [], "confirmed_group_normalization", "derived" if category else "source_missing")
                records.append({"record_id": name + ":" + code_address + (":" + item["pm"] if item["pm"] else ""),
                    "sheet": name, "source_row": row, "equipment_cell": code_address, "pm_key": item["pm"],
                    "equipment_code": code, "raw": raw, "normalized": normalized})
    return {"created_at": now(), "source_sha256": digest(HERE / "source-snapshot.json"),
            "policy_sha256": digest(HERE / "reference-policy.md"), "fields": list(FIELDS), "records": records,
            "reference_method": "independent_deterministic_source_rules_before_ai_call",
            "expert_business_signoff": False}


def prepare_reference():
    if list(HERE.glob("response-*.json")):
        raise SystemExit("Responses already exist. Reference cannot be changed after inference.")
    snapshot = load("source-snapshot.json")
    source = Path(snapshot["original_path"])
    if digest(source) != snapshot["disk_file_sha256"]:
        raise SystemExit("Original disk workbook changed since snapshot.")
    target = HERE / "source-workbook.xlsx"
    if not target.exists():
        shutil.copyfile(source, target)
    if digest(target) != snapshot["disk_file_sha256"]:
        raise SystemExit("Source workbook copy does not match original.")
    reference = build_reference(snapshot)
    save("ground-truth.json", reference)
    product_files = ("src/setron/infrastructure/ai_client.py", "src/setron/infrastructure/excel_reader.py",
        "src/setron/features/ai/service.py", "src/setron/features/ai/schemas.py",
        "src/setron/features/extraction/service.py", "src/setron/features/extraction/normalization.py",
        "src/setron/features/extraction/schemas.py", "pyproject.toml", "uv.lock")
    summary = {"created_at": reference["created_at"], "source_sha256": reference["source_sha256"],
        "reference_sha256": digest(HERE / "ground-truth.json"), "policy_sha256": reference["policy_sha256"],
        "records_by_sheet": dict(Counter(r["sheet"] for r in reference["records"])),
        "total_field_positions": len(reference["records"]) * len(FIELDS),
        "states": {stage: dict(Counter(f["state"] for r in reference["records"] for f in r[stage].values())) for stage in ("raw", "normalized")},
        "code_version": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, encoding="utf-8", check=True).stdout.strip(),
        "product_file_sha256": {name: digest(REPO / name) for name in product_files},
        "benchmark_sha256_at_reference": digest(__file__)}
    save("reference-manifest.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def restore_sheet(sheet):
    from setron.infrastructure.excel_reader import MergedRange, SheetData
    values = decode(sheet)
    values["merged_ranges"] = [MergedRange(**item) for item in values["merged_ranges"]]
    return SheetData(**values)


def run_live(sheet_name, timeout):
    import httpx2
    from setron.features.ai.schemas import ExtractKind
    from setron.features.extraction.service import extract_sheet_data
    from setron.infrastructure.ai_client import AIConfig, AIError, OpenAICompatibleChatClient

    if (HERE / ("response-" + sheet_name + ".json")).exists():
        raise SystemExit("A recorded response already exists; no overwrite or new request performed.")
    manifest = load("reference-manifest.json")
    if digest(HERE / "ground-truth.json") != manifest["reference_sha256"] or digest(HERE / "source-snapshot.json") != manifest["source_sha256"] or digest(HERE / "reference-policy.md") != manifest["policy_sha256"]:
        raise SystemExit("Frozen source/reference/policy changed.")
    config = replace(AIConfig.from_env(), timeout=timeout)
    if config.base_url.rstrip("/") != "https://gms.ssafy.io/gmsapi/api.openai.com/v1":
        raise SystemExit("Unexpected endpoint; no data sent.")
    snapshot = load("source-snapshot.json")
    sheet = restore_sheet(next(s for s in snapshot["sheets"] if s["sheet_name"] == sheet_name))
    captured = {"sheet": sheet_name, "started_at": now(), "model_requested": config.model,
        "base_url": config.base_url, "timeout": timeout, "max_retries": 0,
        "source_sha256": manifest["source_sha256"], "reference_sha256": manifest["reference_sha256"],
        "policy_sha256": manifest["policy_sha256"], "benchmark_sha256": digest(__file__),
        "openai_sdk_version": importlib.metadata.version("openai"), "http_responses": []}
    for name, expected_hash in manifest["product_file_sha256"].items():
        if digest(REPO / name) != expected_hash:
            raise SystemExit("Product code changed after reference freeze: " + name)
    captured["product_file_sha256"] = manifest["product_file_sha256"]

    def redact(value):
        if isinstance(value, str):
            return value.replace(config.api_key, "[REDACTED_API_KEY]")
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value

    def response_hook(response):
        body = response.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        captured["http_responses"].append({"status_code": response.status_code,
            "request_id": response.headers.get("x-request-id"), "body_text": redact(body),
            "usage": payload.get("usage") if isinstance(payload, dict) else None})

    class Recorder:
        def __init__(self, client):
            self.client = client
            self.calls = 0

        def complete(self, messages):
            self.calls += 1
            request = {"sheet": sheet_name, "recorded_at": now(), "base_url": config.base_url,
                "model": config.model, "timeout": timeout, "max_retries": 0,
                "source_sha256": manifest["source_sha256"], "reference_sha256": manifest["reference_sha256"],
                "messages": [asdict(message) for message in messages]}
            save("request-" + sheet_name + ".json", redact(request))
            captured["request_characters"] = sum(len(m.content) for m in messages)
            try:
                reply = self.client.complete(messages)
                captured["model_returned"] = reply.model
                return reply
            except AIError as exc:
                captured["transport_error"] = {"type": type(exc).__name__, "http_status": getattr(exc.__context__, "status_code", None)}
                raise

    start = time.monotonic()
    client = httpx2.Client(event_hooks={"response": [response_hook]})
    result = None
    recorder = None
    try:
        with OpenAICompatibleChatClient(config, http_client=client) as real:
            recorder = Recorder(real)
            result = extract_sheet_data(recorder, ExtractKind.EQUIPMENT_STATUS, sheet)
    except Exception as exc:
        captured["unexpected_error"] = {"type": type(exc).__name__, "message": redact(str(exc))}
    if result is None:
        captured.update({"finished_at": now(), "elapsed_seconds": round(time.monotonic() - start, 3),
            "api_calls": recorder.calls if recorder else 0, "status": "UNHANDLED_ERROR"})
        save("response-" + sheet_name + ".json", redact(captured))
        print(json.dumps({k: v for k, v in captured.items() if k != "http_responses"}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
    captured.update({"finished_at": now(), "elapsed_seconds": round(time.monotonic() - start, 3),
        "api_calls": recorder.calls, "status": result.status.value,
        "source": result.source.value if result.source else None, "response_characters": len(result.raw_content or ""),
        "result_rows": len(result.data.rows) if result.data else 0,
        "review_issues": len(result.data.issues) if result.data else 0})
    save("response-" + sheet_name + ".json", redact(captured))
    save("result-" + sheet_name + ".json", redact(asdict(result)))
    print(json.dumps({k: v for k, v in captured.items() if k != "http_responses"}, ensure_ascii=False), flush=True)
    if not result.ok:
        raise SystemExit(1)


def raw_rows(result):
    content = result.get("raw_content") or ""
    try:
        start, end = content.index("{"), content.rindex("}")
        payload = json.loads(content[start:end + 1])
        return payload["rows"] if isinstance(payload.get("rows"), list) else []
    except (ValueError, TypeError, json.JSONDecodeError):
        return []


def match_rows(reference, actual_rows, sheet, stage):
    expected = {record["record_id"]: record for record in reference}
    by_cell = defaultdict(list)
    by_code = defaultdict(list)
    for record in reference:
        by_cell[record["equipment_cell"]].append(record)
        by_code[record["equipment_code"]].append(record)
    matched, extras = {}, []
    for index, actual in enumerate(actual_rows):
        if not isinstance(actual, dict):
            extras.append((index, {}, "invalid_row"))
            continue
        values = actual.get("values") or {}
        sources = actual.get("sources") or {}
        if not isinstance(values, dict) or not isinstance(sources, dict):
            extras.append((index, {}, "invalid_row_maps"))
            continue
        pointer = sources.get("equipment_code")
        address = pointer.get("address") if isinstance(pointer, dict) else pointer
        source_address = anchor(sheet, address) if address else None
        candidates = by_cell.get(source_address, [])
        method = "source_equipment_cell"
        if not candidates:
            code = values.get("equipment_code")
            candidates = by_code.get(code, []) if isinstance(code, str) else []
            method = "equipment_value_fallback"
        selected = None
        if len(candidates) == 1 and candidates[0]["pm_key"] is None:
            selected = candidates[0]
        elif candidates:
            position = re.fullmatch(r"(?:PM\s*)?([1-9][0-9]*)", str(values.get("pm_name") or ""), re.IGNORECASE)
            if position:
                selected = next((candidate for candidate in candidates if candidate["pm_key"] == "PM" + position[1]), None)
        if selected is None:
            extras.append((index, actual, "no_unique_source_record"))
        elif selected["record_id"] in matched:
            extras.append((index, actual, "duplicate_source_record"))
        else:
            matched[selected["record_id"]] = (index, actual, method)
    return expected, matched, extras


def percent(numerator, denominator):
    return round(100 * numerator / denominator, 4) if denominator else None


def evaluate_stage(records, actual_rows, sheet, stage):
    expected, matched, extras = match_rows(records, actual_rows, sheet, stage)
    comparisons = []
    row_exact = 0
    for record_id, reference in expected.items():
        entry = matched.get(record_id)
        index, actual, method = entry if entry else (None, {}, "missing_record")
        values = actual.get("values") or {}
        sources = actual.get("sources") or {}
        correct_row = entry is not None
        for field in FIELDS:
            target = reference[stage][field]
            value = values.get(field)
            pointer = sources.get(field)
            address = pointer.get("address") if isinstance(pointer, dict) else pointer
            canonical = anchor(sheet, address) if address else None
            scorable = target["state"] != "unresolved"
            if not scorable:
                verdict = "unresolved_abstained" if value is None else "unresolved_returned_value"
                correct = None
            elif entry is None:
                verdict, correct = "missing_record", False
            elif type(value) is type(target["value"]) and value == target["value"]:
                verdict, correct = "correct", True
            else:
                correct = False
                verdict = "omitted_value" if value is None else "invented_value" if target["value"] is None else "mismatch"
            if scorable and not correct:
                correct_row = False
            comparisons.append({"sheet": reference["sheet"], "stage": stage, "record_id": record_id,
                "equipment_cell": reference["equipment_cell"], "pm_key": reference["pm_key"],
                "field": field, "reference_state": target["state"], "reference_rule": target["rule"],
                "expected": target["value"], "actual": value, "expected_sources": target["sources"],
                "actual_source": canonical, "source_matches_reference": canonical in target["sources"] if target["sources"] else None,
                "actual_row_index": index, "alignment_method": method, "scorable": scorable,
                "correct": correct, "verdict": verdict})
        row_exact += int(correct_row)
    for index, actual, reason in extras:
        for field in FIELDS:
            comparisons.append({"sheet": sheet["sheet_name"], "stage": stage, "record_id": "EXTRA:" + str(index),
                "equipment_cell": None, "pm_key": None, "field": field, "reference_state": "extra_record",
                "reference_rule": reason, "expected": None, "actual": (actual.get("values") or {}).get(field),
                "expected_sources": [], "actual_source": None, "source_matches_reference": None,
                "actual_row_index": index, "alignment_method": reason, "scorable": True, "correct": False, "verdict": "extra_record"})
    summary = summarize(comparisons)
    summary.update({"expected_records": len(records), "actual_records": len(actual_rows), "matched_records": len(matched),
        "missing_records": len(expected) - len(matched), "extra_records": len(extras),
        "exact_scorable_records": row_exact, "record_recall_pct": percent(len(matched), len(records)),
        "record_precision_pct": percent(len(matched), len(actual_rows)), "row_exact_pct": percent(row_exact, len(records))})
    return summary, comparisons


def summarize(rows):
    eligible = [row for row in rows if row["scorable"]]
    nonempty = [row for row in eligible if row["expected"] is not None and row["verdict"] != "extra_record"]
    nulls = [row for row in eligible if row["expected"] is None and row["verdict"] != "extra_record"]
    golden = [row for row in rows if row["verdict"] != "extra_record"]
    return {"reference_field_positions": len(golden), "evaluated_fields": len(eligible),
        "correct_fields": sum(row["correct"] is True for row in eligible),
        "accuracy_pct": percent(sum(row["correct"] is True for row in eligible), len(eligible)),
        "reference_coverage_pct": percent(sum(row["scorable"] for row in golden), len(golden)),
        "nonempty_reference_fields": len(nonempty), "correct_nonempty_fields": sum(row["correct"] is True for row in nonempty),
        "nonempty_accuracy_pct": percent(sum(row["correct"] is True for row in nonempty), len(nonempty)),
        "null_reference_fields": len(nulls), "invented_values": sum(row["verdict"] == "invented_value" for row in nulls),
        "unresolved_fields": sum(not row["scorable"] for row in rows),
        "verdict_counts": dict(Counter(row["verdict"] for row in rows))}


def markdown_value(value):
    return json.dumps(value, ensure_ascii=False).replace("|", "\\|").replace("\n", "\\n")


def compare():
    reference = load("ground-truth.json")
    manifest = load("reference-manifest.json")
    if digest(HERE / "ground-truth.json") != manifest["reference_sha256"]:
        raise SystemExit("Reference changed after freezing.")
    snapshots = {sheet["sheet_name"]: sheet for sheet in load("source-snapshot.json")["sheets"]}
    all_rows, summaries = [], {}
    for name, sheet in snapshots.items():
        result = load("result-" + name + ".json")
        call = load("response-" + name + ".json")
        if call["reference_sha256"] != manifest["reference_sha256"] or call["source_sha256"] != manifest["source_sha256"]:
            raise SystemExit("Call reference/snapshot does not match.")
        selected = [record for record in reference["records"] if record["sheet"] == name]
        for stage, actual in (("raw", raw_rows(result)), ("normalized", (result.get("data") or {}).get("rows", []))):
            summary, details = evaluate_stage(selected, actual, sheet, stage)
            summaries[name + ":" + stage] = summary
            all_rows.extend(details)
    overall = {stage: summarize([row for row in all_rows if row["stage"] == stage]) for stage in ("raw", "normalized")}
    by_field = {stage: {field: summarize([row for row in all_rows if row["stage"] == stage and row["field"] == field])
                       for field in FIELDS} for stage in ("raw", "normalized")}
    metrics = {"evaluated_at": now(), "reference_sha256": manifest["reference_sha256"],
        "source_sha256": manifest["source_sha256"], "policy_sha256": manifest["policy_sha256"],
        "benchmark_sha256": digest(__file__), "by_sheet_and_stage": summaries, "overall": overall, "by_field": by_field}
    save("field-comparison.json", all_rows)
    save("metrics.json", metrics)
    text = ["# BE-007 전체 필드 대조표", "", "원본에서 먼저 만든 정답과 실제 응답을 비교했다. 전체 세부 행은 field-comparison.json에 보존한다.", "",
        "| 단계 | 평가 필드 | 일치 | 일치율 | 비어 있지 않은 정답 일치율 | 커버리지 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for stage, score in overall.items():
        text.append(f'| {stage} | {score["evaluated_fields"]} | {score["correct_fields"]} | {score["accuracy_pct"]}% | {score["nonempty_accuracy_pct"]}% | {score["reference_coverage_pct"]}% |')
    text += ["", "## 불일치와 누락", "", "판정 보류는 아래 오류 표에서 제외하고 전체 JSON에 유지한다.", "",
        "| 시트 | 단계 | 원본 레코드 | 필드 | 정답 | 실제 | 판정 | 근거 규칙 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in all_rows:
        if row["correct"] is False:
            text.append("| " + " | ".join([row["sheet"], row["stage"], row["record_id"], row["field"], markdown_value(row["expected"]),
                markdown_value(row["actual"]), row["verdict"], row["reference_rule"]]) + " |")
    (HERE / "field-comparison.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    print(json.dumps({"overall": overall, "by_sheet_and_stage": summaries}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("reference", "run", "compare"))
    parser.add_argument("--sheet", choices=("Memory", "Foundry"))
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if args.action == "reference":
        prepare_reference()
    elif args.action == "run":
        if not args.sheet:
            parser.error("--sheet is required for a live request")
        run_live(args.sheet, args.timeout)
    else:
        compare()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    main()
