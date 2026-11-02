"""보관된 sol/luna 응답만 읽어 같은 조건과 필드별 차이를 검증·비교한다."""

from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from run_luna import verify_shared_inputs

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent / "be-007-evaluation"
SHEETS = ("Memory", "Foundry")
MODELS = (("gpt-6.1-sol", BASELINE), ("gpt-6-luna", HERE))


def load(folder, name):
    return json.loads((folder / name).read_text(encoding="utf-8"))


def save(name, data):
    (HERE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pct(numerator, denominator):
    return round(numerator * 100 / denominator, 4) if denominator else None


def md(value):
    return json.dumps(value, ensure_ascii=False).replace("|", "\\|").replace("\n", "\\n")


def main():
    verify_shared_inputs()
    messages_hashes = {}
    models = {}
    expected = load(HERE, "reference-manifest.json")
    for model, folder in MODELS:
        metrics = load(folder, "metrics.json")
        per_sheet = {}
        for sheet in SHEETS:
            request = load(folder, "request-" + sheet + ".json")
            call = load(folder, "response-" + sheet + ".json")
            baseline_request = load(BASELINE, "request-" + sheet + ".json")
            baseline_call = load(BASELINE, "response-" + sheet + ".json")
            if request["messages"] != baseline_request["messages"]:
                raise SystemExit("Messages differ: " + model + ":" + sheet)
            for key in ("base_url", "timeout", "max_retries", "source_sha256", "reference_sha256"):
                if request[key] != baseline_request[key]:
                    raise SystemExit("Request conditions differ: " + key)
            for key in ("source_sha256", "reference_sha256", "policy_sha256", "benchmark_sha256", "product_file_sha256", "openai_sdk_version"):
                if call[key] != baseline_call[key]:
                    raise SystemExit("Capture conditions differ: " + key)
            if request["model"] != model or call["model_requested"] != model or call.get("model_returned") != model:
                raise SystemExit("Model identity mismatch: " + model + ":" + sheet)
            if call["status"] != "OK" or call["source"] != "REAL" or call["api_calls"] != 1:
                raise SystemExit("Expected exactly one successful real request: " + model + ":" + sheet)
            responses = call["http_responses"]
            if len(responses) != 1 or responses[0]["status_code"] != 200:
                raise SystemExit("Unexpected HTTP outcome: " + model + ":" + sheet)
            usage = responses[0]["usage"]
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if not isinstance(usage.get(field), int):
                    raise SystemExit("Missing token counter: " + field)
            if usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]:
                raise SystemExit("Token totals do not add up.")
            per_sheet[sheet] = {key: call[key] for key in (
                "started_at", "finished_at", "elapsed_seconds", "request_characters", "response_characters", "result_rows", "review_issues")}
            per_sheet[sheet]["usage"] = usage
            messages_hashes[sheet] = sha256(json.dumps(request["messages"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode("utf-8")).hexdigest()
        models[model] = {"bundle": folder.name, "by_sheet": per_sheet,
            "totals": {"elapsed_seconds": round(sum(s["elapsed_seconds"] for s in per_sheet.values()), 3),
                "request_characters": sum(s["request_characters"] for s in per_sheet.values()),
                "response_characters": sum(s["response_characters"] for s in per_sheet.values()),
                "result_rows": sum(s["result_rows"] for s in per_sheet.values()),
                **{field: sum(s["usage"][field] for s in per_sheet.values()) for field in ("prompt_tokens", "completion_tokens", "total_tokens")}},
            "accuracy": {key: metrics[key] for key in ("overall", "by_sheet_and_stage", "by_field")}}
        for parent, key in (("prompt_tokens_details", "cached_tokens"), ("prompt_tokens_details", "cache_write_tokens"),
                            ("completion_tokens_details", "reasoning_tokens")):
            values = [s["usage"].get(parent, {}).get(key) for s in per_sheet.values()]
            models[model]["totals"][key] = sum(values) if all(isinstance(v, int) for v in values) else None
    sol, luna = models["gpt-6.1-sol"], models["gpt-6-luna"]
    changes = {}
    for key in ("elapsed_seconds", "prompt_tokens", "completion_tokens", "total_tokens", "response_characters"):
        before, after = sol["totals"][key], luna["totals"][key]
        changes[key] = {"luna_minus_sol": round(after - before, 4), "change_pct": pct(after - before, before),
            "luna_over_sol_ratio": round(after / before, 6) if before else None}
    for stage in ("raw", "normalized"):
        changes[stage] = {key + "_pp": round(luna["accuracy"]["overall"][stage][key] - sol["accuracy"]["overall"][stage][key], 4)
            for key in ("accuracy_pct", "nonempty_accuracy_pct")}

    comparisons = {}
    extra_fields = []
    for model, folder in MODELS:
        rows = load(folder, "field-comparison.json")
        comparisons[model] = {(r["stage"], r["record_id"], r["field"]): r for r in rows if r["reference_state"] != "extra_record"}
        extra_fields.extend({"model": model, **row} for row in rows if row["reference_state"] == "extra_record")
        models[model]["fixed_reference_accuracy"] = {}
        for stage in ("raw", "normalized"):
            score = models[model]["accuracy"]["overall"][stage]
            denominator = score["reference_field_positions"] - score["unresolved_fields"]
            models[model]["fixed_reference_accuracy"][stage] = {"correct_fields": score["correct_fields"],
                "reference_evaluated_fields": denominator, "accuracy_pct": pct(score["correct_fields"], denominator)}
    if comparisons["gpt-6.1-sol"].keys() != comparisons["gpt-6-luna"].keys():
        raise SystemExit("Expected reference field positions differ.")
    paired = []
    for key, left in comparisons["gpt-6.1-sol"].items():
        right = comparisons["gpt-6-luna"][key]
        for prop in ("expected", "scorable", "reference_state", "expected_sources", "reference_rule"):
            if left[prop] != right[prop]:
                raise SystemExit("Reference field mismatch: " + str(key))
        relation = ("unresolved" if not left["scorable"] else "both_correct" if left["correct"] and right["correct"]
            else "sol_only_correct" if left["correct"] else "luna_only_correct" if right["correct"] else "both_incorrect")
        paired.append({key: left[key] for key in ("sheet", "stage", "record_id", "field", "expected", "expected_sources", "reference_rule", "scorable")}
            | {"relation": relation, "sol": {key: left[key] for key in ("actual", "actual_source", "correct", "verdict")},
                "luna": {key: right[key] for key in ("actual", "actual_source", "correct", "verdict")}})
    pairs = {stage: dict(Counter(row["relation"] for row in paired if row["stage"] == stage)) for stage in ("raw", "normalized")}
    if len(paired) != expected["total_field_positions"] * 2:
        raise SystemExit("Unexpected total reference field count.")
    for stage, counts in pairs.items():
        if counts.get("both_correct", 0) + counts.get("sol_only_correct", 0) != sol["accuracy"]["overall"][stage]["correct_fields"]:
            raise SystemExit("Paired counts disagree with sol accuracy.")
        if counts.get("both_correct", 0) + counts.get("luna_only_correct", 0) != luna["accuracy"]["overall"][stage]["correct_fields"]:
            raise SystemExit("Paired counts disagree with luna accuracy.")
    output = {"created_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "source_sha256": expected["source_sha256"], "reference_sha256": expected["reference_sha256"],
        "policy_sha256": expected["policy_sha256"], "messages_sha256_by_sheet": messages_hashes,
        "comparison_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "conditions_verified_equal": True, "repetitions_per_sheet_per_model": 1,
        "generation_parameters": "Only model and messages sent; temperature, seed and reasoning effort not explicitly specified.",
        "models": models, "luna_minus_sol": changes, "paired_reference_field_counts": pairs,
        "extra_field_errors_by_model": dict(Counter(row["model"] for row in extra_fields))}
    save("model-comparison.json", output)
    save("model-field-comparison.json", paired)
    save("model-extra-records.json", extra_fields)
    text = ["# BE-007 모델별 필드 오류 비교", "", "동일한 정답 필드에 두 모델의 실제 값을 나란히 기록했다. 원문 표기 차이도 사전 고정한 엄격한 계약에 따라 판정한다.", "",
        "전체 필드와 판정 보류의 반환값은 [전체 JSON](model-field-comparison.json), 집계·조건 검증은 [모델 비교 JSON](model-comparison.json)에 보관한다. 대응되지 않은 실제 결과는 [추가 레코드 JSON](model-extra-records.json)과 아래 추가 레코드 표에 남긴다.", "",
        "| 단계 | 둘 다 일치 | sol만 일치 | luna만 일치 | 둘 다 불일치 | 보류 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for stage, counts in pairs.items():
        text.append("| " + stage + " | " + " | ".join(str(counts.get(key, 0)) for key in
            ("both_correct", "sol_only_correct", "luna_only_correct", "both_incorrect", "unresolved")) + " |")
    text += ["", "## 한 모델 이상 불일치한 정답 필드", "", "| 시트 | 단계 | 원본 레코드 | 필드 | 정답 | sol 실제 / 판정 | luna 실제 / 판정 | 정답 근거 셀 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in paired:
        if row["scorable"] and row["relation"] != "both_correct":
            text.append("| " + " | ".join([row["sheet"], row["stage"], row["record_id"], row["field"], md(row["expected"]),
                md(row["sol"]["actual"]) + " / " + row["sol"]["verdict"], md(row["luna"]["actual"]) + " / " + row["luna"]["verdict"],
                ", ".join(row["expected_sources"])]) + " |")
    text += ["", "## 대응되지 않은 실제 결과", "", "missing_record는 기대 PM 위치에 대응하지 못했다는 뜻이다. 실제 반환 행 자체가 사라졌다는 뜻과 구분한다. 위 표의 대응 실패와 아래 추가 결과는 같은 정렬 실패에 따른 누락·추가 페널티일 수 있다.", "",
        "| 모델 | 시트 | 단계 | 결과 인덱스 | 필드 | 실제 값 | 추가 판정 이유 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in extra_fields:
        text.append("| " + " | ".join([row["model"], row["sheet"], row["stage"], row["record_id"], row["field"],
            md(row["actual"]), row["reference_rule"]]) + " |")
    (HERE / "model-field-errors.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    print(json.dumps({"totals": {model: value["totals"] for model,value in models.items()},
        "luna_minus_sol": changes, "paired_reference_field_counts": pairs}, ensure_ascii=False))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    main()
