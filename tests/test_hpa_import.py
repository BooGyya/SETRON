"""BE-024: 합성 증강 HPA 자료의 식별·검토와 공통 N 비교 제외를 검증한다."""

import json

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.service import draft_from_review, has_errors, validate
from setron.features.extraction.service import extract_sheet_data
from setron.infrastructure.ai_client import ExampleChatClient
from setron.infrastructure.excel_reader import SheetData

from test_import_validation import family, reference


def hpa_draft(*, code="TEST-HPA-024", version="vHPA.2_d5 ", model="HPA", group="HPA", family_name="HPA", base_date="2026-11-03"):
    """실제 설비와 무관한 증강 행을 원본 대조까지 거쳐 가져오기 초안으로 만든다.

    param
    - code, version: 시험용 호기·SW 원문. None이면 해당 필수값의 공란을 재현.
    - model, group, family_name: 원본 표에 기록할 모델·설비군·계열명.
    - base_date: 원본에 기록한 관측 기준일.

    return
    - 모의 AI 응답을 원본 셀과 대조한 가져오기 초안.
    """
    fields = ("base_date", "site", "group_name", "model_name", "equipment_code", "family_name", "version_raw", "production_area")
    values = (base_date, "화성", group, model, code, family_name, version, None)
    sheet = SheetData("BE-024_합성_증강자료.xlsx", "HPA 검증", 5, 2,
                      [["기준일", "사업장", "설비군", "모델", "호기", "SW 계열", "SW 버전", "Memory/Foundry"], list(values)])
    payload = {"rows": [{"values": dict(zip(fields, values)),
                         "sources": {field: sheet.address_of(1, index) for index, field in enumerate(fields)}}]}
    result = extract_sheet_data(ExampleChatClient([json.dumps(payload, ensure_ascii=False)]),
                                ExtractKind.EQUIPMENT_STATUS, sheet)
    assert result.ok
    return draft_from_review(result.data, source=result.source)


def test_hpa_is_identified_and_optional_area_is_not_invented():
    """HPA 식별과 호기·SW 원문을 보존하고 선택 공란을 임의로 채우지 않는다."""
    source = hpa_draft()
    assert source.rows[0].value("group_name") == "HPA"
    assert source.rows[0].value("equipment_code") == "TEST-HPA-024"
    assert source.rows[0].value("version_raw") == "vHPA.2_d5 "
    assert source.rows[0].value("production_area") is None
    assert not has_errors(validate(source))


def test_hpa_review_excludes_n_comparison_even_with_registered_target():
    """목표 N이 주어져도 HPA에는 비교 결과 대신 분석 제외 사유를 안내한다."""
    source = hpa_draft(version="v1")
    ref = reference(family(name="HPA", model="HPA", category="HPA"))
    issues = validate(source, ref)
    assert not has_errors(issues)
    assert any(issue.code == "HPA_ANALYSIS_EXCLUDED" for issue in issues)
    assert not any(issue.comparison is not None or issue.code == "VERSION_COMPARISON" for issue in issues)


def test_hpa_unregistered_version_preserves_review_without_n_comparison():
    """미등록 HPA 버전을 저장 가능한 검토 대상으로 두고 N 비교는 생성하지 않는다."""
    issues = validate(hpa_draft(), reference(family(name="HPA", model="HPA", category="HPA")))
    assert not has_errors(issues)
    assert any(issue.code == "RELEASE_UNREGISTERED" for issue in issues)
    assert not any(issue.comparison is not None for issue in issues)
