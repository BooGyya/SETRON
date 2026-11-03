"""현황 보고서를 Excel 파일로 만든다 (명세 BE-021).

시트 구성
- 조회 조건: 기준일, 조회 범위, 모델·계열별 목표 N, 위험·노후도 기준, 생성 시각
- 요약: 물리 설비 수, HPA 제외 수, 적용률, 업그레이드 필요 수, 판단 불가 건수, 위험 후보 수
- 계열별 현황: 계열별 N 이상·미달·판단 불가 건수와 적용률, 묶은 막대 차트
- 상태 구성비: 설비·계열 건수의 상태별 구성, 원형 차트
- 설비별 판정: 설비 1대 × 계열 1행. 설치 버전 원문, 목표 N, N-k, 사유, 위험, 노후도
- 우선순위: BE-017의 순위 목록과 확인 필요 목록
- 날짜별 추이: 두 날짜 이상일 때만. 날짜별 적용률 꺾은선 차트

차트는 모두 같은 시트의 표 셀을 참조하므로 Excel에서 셀을 고치면 차트도 바뀐다.
적용률은 판단 불가를 분모에서 뺀 값이고(BE-012), 계산할 수 없으면 빈 셀에 '계산 불가'를 적는다.
트리맵은 openpyxl이 지원하지 않아 아직 만들지 않는다 (명세의 구현 검증 대기 항목).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from setron.features.aggregation.schemas import FamilyStatus, RateCount
from setron.features.aging.schemas import EquipmentAge
from setron.features.export.schemas import ExportResult, ExportStatus, StatusReport
from setron.features.export.xlsx_cells import ReportTextWriter
from setron.features.risk.schemas import EquipmentRisk, RiskStatus
from setron.features.version.schemas import CompareStatus

STATUS_LABELS = {
    CompareStatus.AT_OR_ABOVE: "N 이상",
    CompareStatus.BELOW: "N 미만",
    CompareStatus.UNDETERMINED: "판단 불가",
}
NOT_CALCULATED = "미계산"
PERCENT = "0.0%"


def build_status_report(report: StatusReport) -> Workbook:
    """보고서 표·차트와 원문을 보존한 통합문서를 만든다.

    긴 문자열은 원래 셀에 좌표 참조를 남기고 마지막 ``긴값_분할`` 시트에
    같은 reference_id의 part_index 순서로 나눠 보존한다. 원문과 같은 참조 문구는
    해당 좌표의 분할 행이 없으면 일반 문자열이다.
    """
    book = Workbook()
    text = ReportTextWriter(book)
    _conditions(book.active, report, text)
    _summary(book.create_sheet("요약"), report, text)
    _families(book.create_sheet("계열별 현황"), report, text)
    _composition(book.create_sheet("상태 구성비"), report, text)
    _equipment(book.create_sheet("설비별 판정"), report, text)
    _priority(book.create_sheet("우선순위"), report, text)
    if len(report.trend) > 1:
        _trend(book.create_sheet("날짜별 추이"), report, text)
    text.finish()
    return book


def save_status_report(report: StatusReport, path: str | Path) -> ExportResult:
    """보고서를 저장하고 실패하면 기존 파일을 보존하며 원인을 안내한다."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .xlsx로 끝나야 합니다.")
    temporary = None
    book = None
    try:
        book = build_status_report(report)
        with NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
        book.save(temporary)
        os.replace(temporary, path)
    except PermissionError:
        message = "파일을 저장할 수 없습니다. 같은 이름의 파일이 Excel에서 열려 있는지, 폴더 쓰기 권한이 있는지 확인해 주세요."
        return ExportResult(ExportStatus.FAILED, path, message)
    except OSError as error:
        return ExportResult(ExportStatus.FAILED, path, f"파일을 저장하지 못했습니다: {error.strerror or error}")
    except (ValueError, TypeError) as error:
        return ExportResult(ExportStatus.FAILED, path, f"현황 자료를 XLSX로 보존할 수 없습니다: {error}")
    finally:
        if book is not None:
            book.close()
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return ExportResult(ExportStatus.SAVED, path)


# --- 시트 -----------------------------------------------------------------------

def _conditions(sheet: Worksheet, report: StatusReport, text: ReportTextWriter) -> None:
    sheet.title = "조회 조건"
    base_date = report.summary.base_date
    created = report.created_at or datetime.now()
    _rows(sheet, ["항목", "값"], [
        ["기준일", base_date.isoformat() if base_date else "기준일 없음"],
        *([name, value] for name, value in report.scope),
        ["위험 기준", _risk_rule_note(report)],
        ["노후도 기준", _age_rule_note(report)],
        ["적용률 계산", "N 이상 ÷ (N 이상 + N 미만). 판단 불가는 분모에서 빼고 따로 적습니다."],
        ["생성 시각", created.strftime("%Y-%m-%d %H:%M")],
    ], text=text)
    start = sheet.max_row + 2
    sheet.cell(start, 1, "모델·계열별 목표 N").font = Font(bold=True)
    _rows(sheet, ["모델", "계열", "목표 N"],
          [[t.model_name, t.family_name, t.version_raw or "미지정 (판단 불가)"] for t in report.targets],
          start=start + 1, text=text)


def _summary(sheet: Worksheet, report: StatusReport, text: ReportTextWriter) -> None:
    summary = report.summary
    risk_count = (sum(e.status is RiskStatus.RISK for e in report.risks.equipment)
                  if report.risks else NOT_CALCULATED)
    _rows(sheet, ["항목", "값", "설명"], [
        ["물리 설비 수", summary.equipment_count, "HPA를 뺀 호기 수. 같은 호기는 한 대"],
        ["분석 제외 (HPA)", summary.hpa_excluded, "집계에서 뺀 HPA 호기 수"],
        ["N 이상 적용률", *_rate(summary)],
        ["업그레이드 필요", summary.upgrade_needed, "한 계열이라도 N 미만인 호기 수"],
        ["N 이상 (건)", summary.compliant, "설비 1대 × 계열 1건"],
        ["N 미만 (건)", summary.below, "설비 1대 × 계열 1건"],
        ["판단 불가 (건)", summary.undetermined, "버전·릴리즈·목표 N을 확인할 수 없는 건. 안전으로 보지 않음"],
        ["위험 후보 설비", risk_count, "목표 달성 여부와 별개"],
    ], text=text)
    sheet["B4"].number_format = PERCENT


def _families(sheet: Worksheet, report: StatusReport, text: ReportTextWriter) -> None:
    counts = report.summary.by_family
    _rows(sheet, ["계열", "N 이상", "N 미만", "판단 불가", "적용률", "비고"], [
        [_family_name(report, c.family_id), c.compliant, c.below, c.undetermined, *_rate(c)] for c in counts
    ], text=text)
    _percent_column(sheet, "E", len(counts))
    if not counts:
        return
    chart = BarChart()
    chart.title = "계열별 N 이상·미달·판단 불가 (건)"
    chart.y_axis.title = "건수"
    chart.add_data(Reference(sheet, min_col=2, max_col=4, min_row=1, max_row=len(counts) + 1), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=len(counts) + 1))
    sheet.add_chart(chart, "H2")


def _composition(sheet: Worksheet, report: StatusReport, text: ReportTextWriter) -> None:
    summary = report.summary
    _rows(sheet, ["상태", "건수"], [
        ["N 이상", summary.compliant],
        ["N 미만", summary.below],
        ["판단 불가", summary.undetermined],
    ], text=text)
    sheet["D1"] = "설비 1대 × 계열 1건 기준. 판단 불가도 포함한 전체 건수의 구성이라 적용률과 분모가 다릅니다."
    chart = PieChart()
    chart.title = "상태 구성비 (건)"
    chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=4), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=4))
    sheet.add_chart(chart, "D3")


def _equipment(sheet: Worksheet, report: StatusReport, text: ReportTextWriter) -> None:
    risks = {r.record.equipment_id: r for r in report.risks.equipment} if report.risks else None
    ages = {a.record.equipment_id: a for a in report.ages} if report.ages is not None else None
    header = ["설비", "Site", "Line", "모델", "계열", "설치 버전", "목표 N", "판정", "사유", "PM 버전 불일치",
              "위험", "위험 사유", "노후도", "경과 기간"]
    rows = []
    for equipment in report.summary.equipment:
        record = equipment.record
        risk = _risk_cells(risks.get(record.equipment_id) if risks is not None else None, risks is not None)
        for family in equipment.families:
            age = _age_cells(ages.get(record.equipment_id) if ages is not None else None, family.family_id,
                             ages is not None)
            rows.append([
                record.equipment_code, record.site, record.line or "", record.model_name,
                _family_name(report, family.family_id), _versions(family), _target(family),
                _family_label(family), _reason(family), "예" if family.pm_mismatch else "", *risk, *age,
            ])
    _rows(sheet, header, rows, text=text)


def _priority(sheet: Worksheet, report: StatusReport, text: ReportTextWriter) -> None:
    result = report.priority
    if result is None:
        sheet["A1"] = "우선순위를 계산하지 않았습니다."
        return
    _rows(sheet, ["순위", "설비", "위험", "위험 사유", "대표 경과 기간", "대표 계열", "비고"], [
        [e.rank, e.record.equipment_code, e.risk.label, _reasons(e.risk),
         e.representative.age.elapsed_label, _family_name(report, e.representative.pm.family_id),
         "일부 계열 기간 미확인" if e.partial_age else ""]
        for e in result.ranked
    ], text=text)
    notes = []
    if not result.has_rules:
        notes.append("등록된 위험 기준이 없어 노후도로만 정렬했습니다.")
    if result.hpa_excluded:
        notes.append(f"HPA {result.hpa_excluded}대는 제외했습니다.")
    start = sheet.max_row + 2
    for offset, note in enumerate(notes):
        sheet.cell(start + offset, 1, note)
    start += len(notes) + (1 if notes else 0)
    sheet.cell(start, 1, "확인 필요 (순위 없음)").font = Font(bold=True)
    _rows(sheet, ["설비", "위험", "위험 사유", "빠진 근거"], [
        [e.record.equipment_code, e.risk.label, _reasons(e.risk), " ".join(e.messages)] for e in result.needs_check
    ], start=start + 1, text=text)


def _trend(sheet: Worksheet, report: StatusReport, text: ReportTextWriter) -> None:
    trend = report.trend
    _rows(sheet, ["기준일", "N 이상", "N 미만", "판단 불가", "적용률", "비고"], [
        [s.base_date, s.compliant, s.below, s.undetermined, *_rate(s)] for s in trend
    ], text=text)
    for row in range(2, len(trend) + 2):
        sheet.cell(row, 1).number_format = "yyyy-mm-dd"
    _percent_column(sheet, "E", len(trend))
    sheet["H1"] = "기록이 있는 날짜만 적습니다. 빈 날짜를 이전 값으로 채우지 않습니다."
    chart = LineChart()
    chart.title = "날짜별 N 이상 적용률"
    chart.y_axis.number_format = PERCENT
    chart.add_data(Reference(sheet, min_col=5, min_row=1, max_row=len(trend) + 1), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=len(trend) + 1))
    sheet.add_chart(chart, "H3")


# --- 셀 값 ----------------------------------------------------------------------

def _rows(sheet: Worksheet, header: Sequence[str], rows: Iterable[Sequence], start: int = 1,
          *, text: ReportTextWriter) -> None:
    for column, title in enumerate(header, start=1):
        cell = sheet.cell(start, column)
        text.write(cell, title, title)
        cell.font = Font(bold=True)
    for offset, row in enumerate(rows, start=1):
        for column, value in enumerate(row, start=1):
            text.write(sheet.cell(start + offset, column), value, header[column - 1])


def _rate(count: RateCount) -> list:
    """적용률 셀과 비고. 계산할 수 없으면 숫자 셀을 비워 차트가 0%로 그리지 않게 한다."""
    if count.rate is None:
        return [None, "계산 불가: 판단 가능한 건이 없습니다."]
    return [count.rate, "" if count.complete or not count.undetermined
            else f"판단 불가 {count.undetermined}건은 분모에서 뺐습니다."]


def _percent_column(sheet: Worksheet, column: str, count: int) -> None:
    for row in range(2, count + 2):
        sheet[f"{column}{row}"].number_format = PERCENT


def _family_name(report: StatusReport, family_id: int) -> str:
    return report.family_names.get(family_id, f"계열 {family_id}")


def _versions(family: FamilyStatus) -> str:
    def text(p):
        raw = p.pm.version_raw or "(버전 없음)"
        return f"{p.pm.pm_name} {raw}" if p.pm.pm_name else raw
    return " / ".join(text(p) for p in family.pms)


def _target(family: FamilyStatus) -> str:
    targets = {p.comparison.target.version_raw for p in family.pms if p.comparison.target}
    return " / ".join(sorted(targets)) or "미지정"


def _family_label(family: FamilyStatus) -> str:
    """계열 판정. 미달이면 PM 중 가장 낮은 단계를 적고, 단계를 모르는 PM이 있으면 'N 미만'으로 적는다."""
    if family.status is not CompareStatus.BELOW:
        return STATUS_LABELS[family.status]
    below = [p.comparison for p in family.pms if p.comparison.status is CompareStatus.BELOW]
    if any(c.steps_below is None for c in below):
        return "N 미만"
    return f"N-{max(c.steps_below for c in below)}"


def _reason(family: FamilyStatus) -> str:
    messages = dict.fromkeys(p.comparison.message for p in family.pms if p.comparison.message)
    return " ".join(messages)


def _reasons(risk: EquipmentRisk) -> str:
    return " / ".join(dict.fromkeys(f.rule.risk_reason for f in risk.reasons))


def _risk_cells(risk: EquipmentRisk | None, calculated: bool) -> list[str]:
    if not calculated:
        return [NOT_CALCULATED, ""]
    if risk is None:
        return ["미확인", "위험 판정 결과가 없습니다."]
    return [risk.label, _reasons(risk)]


def _age_cells(age: EquipmentAge | None, family_id: int, calculated: bool) -> list[str]:
    """계열의 노후도. 같은 계열 PM이 여러 개면 경과 기간이 가장 긴 PM을 적는다."""
    if not calculated:
        return [NOT_CALCULATED, ""]
    pms = [p for p in (age.pms if age else ()) if p.pm.family_id == family_id]
    if not pms:
        return ["미확인", ""]
    known = [p for p in pms if p.age.elapsed_days is not None]
    chosen = max(known, key=lambda p: p.age.elapsed_days) if known else pms[0]
    return [chosen.age.label, chosen.age.elapsed_label or ""]


def _risk_rule_note(report: StatusReport) -> str:
    if report.risks is None:
        return NOT_CALCULATED
    if not report.risks.has_rules:
        return "등록된 위험 기준이 없습니다. '위험 없음'은 확인된 결과가 아닙니다."
    return "등록된 위험 기준으로 판정"


def _age_rule_note(report: StatusReport) -> str:
    if report.ages is None:
        return NOT_CALCULATED
    if report.thresholds is None:
        return "저장된 기간 기준이 잘못되어 주의·위험을 표시하지 않습니다. 경과 기간은 적습니다."
    return report.thresholds.description
