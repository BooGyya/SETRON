"""VER5 보고서 읽기 모델. 생성 시 조건·관측·N을 고정하며 업무 판정은 기존 서비스를 쓴다."""

from dataclasses import dataclass
from datetime import date, datetime
from ntpath import isreserved

from setron.features.chat.schemas import Table
from setron.features.item.schemas import APPLICATION_LABELS, BASIS_LABELS
from setron.features.item.service import ItemCatalog
from setron.features.priority.service import prioritize_from_risks
from setron.features.risk.schemas import EquipmentRisk, RiskStatus, RiskSummary
from setron.features.targets.groups import with_saved_groups
from setron.features.version.groups import SLOT_LABELS
from setron.ui.dashboard_data import family_name, missing_sw_count
from setron.ui.query_state import UNASSIGNED, AppliedQuery, selection_keys
from setron.ui.replay_data import ReplayRequest, load_replay
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.report_visual_data import grouped_report_heatmaps, hpa_rows, report_visuals


REPORT_TYPES = (
    ("status", "현황·표준화", "버전 표준화 리포트", "Site·설비군별 표준화율과 N 적용률 추이", "10542.svg"),
    ("upgrade", "업그레이드", "업그레이드 현황 리포트", "업그레이드 필요 설비와 진행 현황", "d2feb.svg"),
    ("item", "Item 배포", "릴리즈 적용 현황 리포트", "선택한 Item의 설비별 적용 현황", "c7361.svg"),
    ("change", "변경 이력", "설비별 변경 이력 리포트", "기간 내 설비별 버전 변경 내역", "57213.svg"),
    ("ai", "AI 분석", "AI 분석 리포트", "AI 분석 탭에서 완료한 가장 최근 답변", "4c3e7.svg"),
)
FORMATS = (("docx", "Word", "Word (.docx)"), ("pdf", "PDF", "PDF (.pdf)"))
COMMON_CONTENTS = (
    ("summary", "요약", "조회 조건, 목표 N, 설비 수, 달성률, 위험 현황"),
    ("trend", "적용률 추이", "전체 및 라인별 적용률 그래프와 수치"),
    ("heatmap", "히트맵", "선택한 묶음별 설비명, 버전 색상과 위험 표시"),
    ("distribution", "구성비와 분포", "설비군별 구성비, 막대, 트리맵"),
    ("risk", "위험 판정 근거", "위험 후보 설비와 판정 근거"),
    ("evidence", "관측 버전과 원문 근거", "설비별 관측 버전, HPA 분석 제외 현황"),
)


def safe_filename(value):
    """출력 폴더를 벗어나는 이름·Windows 예약 이름을 허용하지 않는다."""
    value = value.strip()
    if (not value or value in {".", ".."} or value.endswith((".", " "))
            or any(c in value for c in '<>:"/\\|?*') or any(ord(c) < 32 for c in value)
            or isreserved(value)):
        raise ValueError("파일명에 사용할 수 없는 문자 또는 이름입니다.")
    if len(value) > 120:
        raise ValueError("파일명은 120자 이내로 입력해 주세요.")
    return value


@dataclass(frozen=True)
class ReportRequest:
    kind: str
    query: AppliedQuery
    base_date: date
    start: date
    end: date
    format: str = "docx"
    filename: str = "SETRON_보고서"
    heatmap: bool = True
    distribution: bool = True
    trend: bool = True
    evidence: bool = True
    item_id: int | None = None
    ai_answer: object = None
    numeric_only: bool = False
    demo: str = ""  # 명시적으로 켠 UI 시연만. 원본 공급자를 수정하지 않는다.
    # None은 기존 호출의 단일 유형/표시 옵션을 사용한다. 빈 튜플은 명시적 미선택이다.
    contents: tuple[str, ...] | None = None
    types: tuple[str, ...] | None = None
    heatmap_modes: tuple[str, ...] = ("group",)

    @property
    def selected_contents(self):
        if self.contents is not None:
            return self.contents
        return ("summary", "risk", *(key for key in ("trend", "heatmap", "distribution", "evidence")
                                     if getattr(self, key)))

    @property
    def selected_types(self):
        return (self.kind,) if self.types is None else self.types

    def __post_init__(self):
        if self.kind not in {r[0] for r in REPORT_TYPES} or self.format not in {r[0] for r in FORMATS}:
            raise ValueError("보고서 유형·출력 형식을 확인해 주세요.")
        if any(type(day) is not date for day in (self.base_date, self.start, self.end)) or self.start > self.end:
            raise ValueError("기준일과 기간을 YYYY-MM-DD 형식의 유효한 날짜로 입력해 주세요.")
        safe_filename(self.filename)
        for field, options in (("contents", COMMON_CONTENTS), ("types", REPORT_TYPES),
                               ("heatmap_modes", GROUPING_MODES)):
            values = getattr(self, field)
            if values is not None:
                if any(value not in {o[0] for o in options} for value in values):
                    raise ValueError("보고서 포함 항목과 히트맵 보기를 확인해 주세요.")
                object.__setattr__(self, field, tuple(dict.fromkeys(values)))
        if not self.selected_contents and not self.selected_types:
            raise ValueError("보고서에 포함할 내용을 하나 이상 선택해 주세요.")
        if "heatmap" in self.selected_contents and not self.heatmap_modes:
            raise ValueError("히트맵 보기를 하나 이상 선택해 주세요.")
        if "item" in self.selected_types and self.item_id is None:
            raise ValueError("조회할 Item을 선택해 주세요.")
        if "ai" in self.selected_types and not self.numeric_only and self.ai_answer is None and self.demo != "ai":
            raise ValueError("AI 분석에서 완료한 답변을 불러오거나 ‘AI 응답 없이 수치표만 생성’을 선택해 주세요.")


@dataclass(frozen=True)
class ReportSection:
    tab: int
    title: str
    note: str = ""
    table: Table | None = None
    chart: str = ""  # distribution / trend / line_trend / heatmap
    visual: object = None
    content_key: str = ""


@dataclass(frozen=True)
class GeneratedReport:
    request: ReportRequest
    created: datetime
    title: str
    sections: tuple[ReportSection, ...]
    scope: str
    source: str

    @property
    def filename(self):
        return f"{self.request.filename}.{self.request.format}"


def _caption(options, value, all_text):
    names = {option.key: option.label for option in options}
    return ", ".join("미지정" if key is UNASSIGNED else names.get(key, str(key))
                     for key in selection_keys(value)) or all_text


def scope_caption(catalog, selection):
    return " · ".join((_caption(catalog.sites, selection.site_key, "전체 Site"),
                       _caption(catalog.fabs, selection.fab_key, "분류 전체"),
                       _caption(catalog.lines, selection.line_key, "Line 전체"),
                       _caption(catalog.groups, selection.group_key, "설비군 전체")))


def unavailable_report_contents(report):
    """선택한 내용의 실제 출력 자료를 확인한다. 0%나 미확인 값은 누락으로 보지 않는다."""
    missing = []
    sections = report.sections
    captions = {key: title for key, title, *_ in (*COMMON_CONTENTS, *REPORT_TYPES)}
    for key in (*report.request.selected_contents, *report.request.selected_types):
        chosen = tuple(section for section in sections if section.content_key == key)
        if key == "heatmap":
            for mode in report.request.heatmap_modes:
                title = "설비군·라인별 히트맵" if mode == "group" else f"{dict(GROUPING_MODES)[mode]}별 히트맵"
                if not any(s.title == title and s.visual for s in chosen):
                    missing.append(title)
            continue
        if key == "summary":
            available = any(s.title == "조회 조건과 집계 기준" and s.table and s.table.rows
                            and s.table.rows[0][0] > 0 for s in chosen)
        elif key == "trend":
            available = any(s.chart in {"trend", "line_trend"} and s.table and s.table.rows for s in chosen)
        elif key == "ai":
            available = report.request.ai_answer is not None and not report.request.numeric_only
        else:
            available = any(s.table and s.table.rows for s in chosen
                            if key != "item" or s.title.startswith("Item 배포"))
        if not available:
            missing.append(captions[key])
    return tuple(missing)


def generate_report(source, request, versions=None):
    """읽기 공급자만 호출. 날짜 보간·N 변경·AI 재호출·DB 쓰기는 하지 않는다."""
    if request.demo == "generation_failure":
        raise RuntimeError("UI 시연 · 보고서 생성 실패. 생성 재시도로 같은 조건을 다시 시도하세요.")
    replay = load_replay(source, ReplayRequest(min(request.start, request.base_date),
                                             max(request.end, request.base_date), request.query))
    frozen = replay.source
    frames = tuple(f for f in replay.frames if request.start <= f.day <= request.end)
    current = next((f for f in replay.frames if f.day == request.base_date), None)
    if request.demo == "empty":
        current, frames = None, ()
    elif request.demo == "single":
        frames = (current,) if current else ()
    snapshot = current.snapshot if current else None
    summary = snapshot.summary if snapshot else None
    records = tuple(r for r in (snapshot.selected_records if snapshot else ()) if not r.is_hpa)
    counts = (summary.compliant, summary.below, summary.undetermined) if summary else (0, 0, 0)
    scope = scope_caption(frozen.catalog, request.query.selection)
    source_label = ("UI 시연 · " if request.demo else "") + frozen.source_label
    sections = []

    selected = set(request.selected_contents)

    def add(tab, title, note="", columns=(), rows=(), chart="", visual=None, *, content="summary"):
        if content not in selected and content not in request.selected_types:
            return
        sections.append(ReportSection(tab, title, note, Table(title, tuple(columns), tuple(tuple(r) for r in rows))
                                      if columns else None, chart, visual, content))

    line_trends, heatmaps, line_counts, risk_rows = report_visuals(frozen, frames, current)

    missing = missing_sw_count(summary) if summary else 0
    total = summary.equipment_count if summary else 0
    excluded = summary.hpa_excluded if summary else sum(r.is_hpa for r in frozen._scope_records(
        tuple(r for r in frozen.records if r.base_date == request.base_date), request.query))
    if request.demo == "empty":
        excluded = 0
    rate = summary.rate if summary else None
    add(0, "조회 조건과 집계 기준", "조회 범위의 분모와 미확인·분석 제외 수입니다.",
        ("물리 설비(대)", "버전 판정(건)", "N 이상(건)", "N 미만(건)", "판단 불가(건)"),
        ((total, sum(counts), *counts),))
    add(0, "목표 N 달성률", f"{'계산 불가' if rate is None else f'{rate:.1%}'} · 분모 {counts[0] + counts[1]}건 · "
        f"판단 불가 {counts[2]}건 · SW 미등록 {missing}대 · HPA 제외 {excluded}대. "
        "달성률=N 이상/(N 이상+N 미만). 물리 설비 수와 버전 판정 건수는 다릅니다.")
    add(0, "결함 위험 판정", "목표 N 달성과 별도입니다. 위험 기준이 없는 설비를 ‘안전’으로 판정하지 않습니다.",
        ("위험 후보(대)", "등록 규칙상 후보 아님(대)", "위험 미확인(대)"),
        (tuple(sum(frozen.risk_status(r) is status for r in records)
               for status in (RiskStatus.RISK, RiskStatus.NO_RISK, RiskStatus.UNKNOWN)),))
    targets = snapshot.target_values if snapshot else request.query.targets or ()
    target_rows = []
    for target in targets:
        family = frozen.families.get(target.family_id)
        release = family.get(target.release_id) if family and target.release_id else None
        target_rows.append((f"모델 {target.model_id} · {family_name(frozen, target.family_id)}",
                            release.version_raw if release else "목표 미지정"))
    for (model_id, family_id), group in frozen.saved_groups.items():
        family = frozen.families.get(family_id)
        for slot, members in sorted(group.slots.items()):
            raw = tuple(family.get(rid).version_raw if family and family.get(rid) else f"릴리즈 {rid} 미확인"
                        for rid in sorted(members))
            target_rows.append((f"모델 {model_id} · {family_name(frozen, family_id)} · {SLOT_LABELS[slot]}", " · ".join(raw)))
    add(0, "생성 당시 고정 조건", "자료·목표 N·판정 규칙은 생성 당시 기준입니다. N 달성과 결함 위험은 별도입니다.",
        ("조건", "값"), (("관측 기준일", str(request.base_date)), ("조회 범위", scope),
        ("기간", f"{request.start} ~ {request.end}"), ("자료 출처", source_label),
        ("실제 관측 날짜 수", len(frames)),
        ("N 그룹", "모델·계열별 저장 기준 적용" if frozen.saved_groups else "저장 그룹 없음 · 개별 목표 N 적용"), *target_rows))
    if current is None:
        add(0, "자료 없음", "선택한 기준일에 저장된 관측 자료가 없습니다. 다른 날짜의 자료나 0%로 대신하지 않습니다.")
    rows = tuple((str(f.day), f.snapshot.summary.compliant, f.snapshot.summary.below,
                  f.snapshot.summary.undetermined, f.snapshot.summary.rate) for f in frames)
    add(1, "날짜별 적용률 추이", "선택 범위 전체 추세입니다. 실제 관측일만 표시하며 미관측·분모 없음은 비워 둡니다."
        + (" 관측이 1장 이하이므로 추세·예측을 만들지 않습니다." if len(frames) < 2 else ""),
        ("관측일", "N 이상", "N 미만", "판단 불가", "적용률"), rows,
        "trend" if len(frames) >= 2 else "", content="trend")
    if not line_trends:
        add(1, "라인별 적용률 추이", "선택한 기간과 조건에 해당하는 관측 자료가 없습니다.", content="trend")
    for trend in line_trends:
        title = f"{trend.area} · {trend.site} · {trend.group} 라인별 적용률 추이"
        add(1, title, "같은 설비군의 실제 라인별 N 이상 적용률입니다. 미관측·판정 불가는 선을 끊고, 관측 1장은 점으로 표시합니다.",
            ("관측일", *trend.lines),
            tuple((str(day), *(rates[i] for rates in trend.rates)) for i, day in enumerate(trend.days)),
            "line_trend" if trend.days else "", trend, content="trend")
        latest = tuple(row for row in line_counts if row[:3] == (trend.area, trend.site, trend.group))
        add(1, f"{trend.area} · {trend.site} · {trend.group} 라인별 적용 현황",
            "기준일 현재 값입니다. 달성률=N 이상/(N 이상+N 미만). 여러 설비군에 연결된 설비는 각 설비군에 표시됩니다.",
            ("라인", "설비(대)", "N 이상(건)", "판단 불가(건)", "적용률", "위험 후보(대)"),
            tuple((row[3], row[4], row[5], row[7], row[8], row[9]) for row in latest), content="trend")
    if "distribution" in selected:
        # 설비군별 판정 건수다. 복수 설비군 소속은 각각 집계하며 물리 설비 대수와 구분한다.
        grouped = {}
        for row in line_counts:
            values = grouped.setdefault(row[2], [0, 0, 0])
            for i, value in enumerate(row[5:8]):
                values[i] += value
        rows = tuple((name, *values) for name, values in grouped.items())
        add(1, "구성비·막대·트리맵", "설비군별 SW 판정 건수입니다. DFB는 C4와 별도 표시하며 복수 설비군 소속은 각각 표시합니다.",
            ("설비군", "N 이상", "N 미만", "판단 불가"), rows, "distribution", content="distribution")
    if "heatmap" in selected:
        for mode in request.heatmap_modes:
            panels = grouped_report_heatmaps(frozen, current, mode, heatmaps)
            caption = dict(GROUPING_MODES)[mode]
            title = "설비군·라인별 히트맵" if mode == "group" else f"{caption}별 히트맵"
            add(1, title, f"{caption} 기준 묶음입니다. 채움색은 버전 단계, 붉은 테두리와 ‘위험’은 별도 위험 후보입니다. "
                "HPA는 분석에서 제외합니다."
                + (" 실제 세부라인이 저장된 설비만 표시합니다." if mode == "subline" else "")
                + (" 표시할 관측 자료가 없습니다." if not panels else ""),
                ("묶음", "라인", "설비", "버전 단계", "위험"),
                tuple((panel.group, line, cell.code, cell.tier.value, cell.risk.value)
                      for panel in panels for line, cells in panel.lines for cell in cells),
                "heatmap", panels, content="heatmap")
    add(2, "위험 후보 설비와 근거", "목표 달성 여부와 별도입니다. 위험 후보가 없더라도 위험 미확인 설비를 안전으로 확정하지 않습니다.",
        ("분류", "사업장", "설비군", "라인", "설비", "위험", "근거"), risk_rows, content="risk")
    excluded_rows = hpa_rows(current, frozen, request.query, request.base_date) if request.demo != "empty" else ()
    if excluded_rows:
        add(2, "HPA 관측 현황 · 분석 제외", "저장된 호기·라인·버전은 보존하며 목표 적용률과 위험 분석의 분모에서는 제외합니다.",
            ("사업장", "분류", "라인", "설비", "관측일", "관측 버전", "비고"), excluded_rows, content="evidence")
    if "status" in request.selected_types:
        add(2, "현황 및 표준화", "선택 범위의 설비군·라인별 N 적용 현황입니다. 물리 설비 수와 SW 판정 건수는 구분합니다.",
            ("분류", "사업장", "설비군", "라인", "설비(대)", "N 이상(건)", "N 미만(건)", "판단 불가(건)", "적용률"),
            tuple(row[:9] for row in line_counts), content="status")
    if "item" in request.selected_types:
        if versions is None or not any(e.item.item_id == request.item_id for e in versions.items):
            raise ValueError("선택한 Item이 현재 릴리즈 자료에 없습니다. Item 목록을 다시 불러와 주세요.")
        item = next(e for e in versions.items if e.item.item_id == request.item_id)
        catalog = ItemCatalog(versions.families(), versions.version_items)
        applications = tuple(catalog.equipment_application(request.item_id, r) for r in records)
        add(2, f"Item 배포 · {item.item.item_no or request.item_id}", item.item.content + "\n목표 N 달성과는 별도 판정입니다.",
            ("설비", "Item 적용 결과", "판정 근거"), tuple((a.record.equipment_code, APPLICATION_LABELS[a.application],
             " · ".join(dict.fromkeys(BASIS_LABELS[p.basis] for p in a.pms))) for a in applications), content="item")
        add(2, "Item 원본 근거", "제공된 원본 위치만 표시합니다.", ("항목", "문서", "리비전", "시트", "셀"),
            tuple((s.field, s.document or "미확인", s.revision or "미확인", s.sheet or "미확인", s.cells or "미확인")
                  for s in item.sources), content="item")
    if "upgrade" in request.selected_types:
        risks = RiskSummary(tuple(EquipmentRisk(r, frozen.risk_status(r), ()) for r in records),
                            has_rules=any(frozen.risk_status(r) is not RiskStatus.UNKNOWN for r in records))
        priority = prioritize_from_risks(records, risks, frozen.families, None, request.base_date,
                                         targets=with_saved_groups(frozen.targets, frozen.saved_groups))
        upgrade_ids = {e.record.equipment_id for e in summary.equipment if e.needs_upgrade} if summary else set()
        candidates = tuple(e for e in (*priority.ranked, *priority.needs_check)
                           if e.record.equipment_id in upgrade_ids or e.is_risk or e.gaps)
        add(2, "업그레이드 필요 설비", "위험 확인 설비 우선 → N보다 낮은 단계순. 근거가 없으면 순위를 확정하지 않습니다.",
            ("우선순위", "설비", "위험", "N 대비 부족 단계", "판정 근거"),
            tuple((e.rank if e.rank is not None else "확인 필요", e.record.equipment_code, e.risk.label,
                   e.steps_behind if e.steps_behind is not None else "미확인", " · ".join(e.messages) or "확인된 릴리즈 순서")
                  for e in candidates), content="upgrade")
    if "change" in request.selected_types:
        add(2, "설비별 변경 이력", "실제 패치 시각이 아닌 두 관측일 사이의 버전 차이입니다. 관측이 없으면 변경을 추정하지 않습니다.",
            ("이전 관측일", "다음 관측일", "설비", "PM · 설비군", "이전 버전", "다음 버전"), _changes(frames, frozen), content="change")
        if len(frames) < 2:
            add(2, "변경 판단 불가", "관측이 1장 이하입니다. 두 관측일이 있는 기간을 선택하세요.", content="change")
    if "evidence" in selected:
        add(2, "설비 목록·원문 근거", "관측 SW 원문입니다. 원본 위치 미연결 · 임의 생성 안 함. 관리값 수정 전 원본과의 대조는 제공된 근거만 사용합니다.",
            ("설비", "관측일", "Site", "원본 구역", "PM", "관측 버전 원문", "원본 위치"),
            tuple((r.equipment_code, str(r.base_date), r.site, r.line or "미지정", pm.pm_name or "일반 SW",
                   pm.version_raw or "미입력", "원본 위치 미연결")
                  for r in records for pm in r.pms), content="evidence")
    if "ai" in request.selected_types and not request.numeric_only:
        answer = request.ai_answer
        if request.demo == "ai":
            add(3, "AI 분석 · UI 시연 예제", f"UI 시연 전용 · 회사 AI 결과가 아닙니다. 선택 범위 {total}대, N 미만 {counts[1]}건. "
                "이 문장은 저장된 수치표를 설명하는 예제이며 원인·완료 시점은 추정하지 않습니다.", content="ai")
        else:
            add(3, "AI 분석 · 근거·추정·미확인", f"질문: {answer.request.question}\nAI 응답 자체 조회 범위: {answer.understood}\n"
                f"응답 기준일: {answer.base_date or '미확인'}\n{answer.summary}\n{answer.cause or ''}\n"
                "AI 응답과 보고서 조건·수치가 일치하는지 확인 필요. 보고서 생성은 AI를 다시 호출하지 않습니다.", content="ai")
            for table in answer.tables:
                indices = [i for i, name in enumerate(table.columns) if name.replace(" ", "") not in {"SW계열", "버전계열"}]
                if indices:
                    add(3, table.title, "기존 AI 응답의 표 · 보고서 공통 집계와 별도",
                        tuple(table.columns[i] for i in indices), tuple(tuple(row[i] for i in indices) for row in table.rows), content="ai")
            notices = tuple(dict.fromkeys((*answer.issues, *answer.notices)))
            if notices:                         # 주의 문장마다 제목을 반복하지 않고 한 항목에 목록으로 (11/16 멘토)
                add(3, "AI 주의 사항", "\n".join(f"· {notice}" for notice in notices), content="ai")
        add(3, "예측 안내", "참고용 계산이며 완료 약속이 아닙니다. 판단 불가·미관측·목표 미지정은 별도 표시하고 자료가 부족하면 예측을 생략합니다.", content="ai")
    elif "ai" in request.selected_types:
        add(3, "AI 분석 결과 없음", "AI 분석에서 완료한 답변이 없습니다. AI를 다시 호출하거나 답변을 임의로 생성하지 않습니다.", content="ai")
    title = (next(r[2] for r in REPORT_TYPES if r[0] == request.selected_types[0])
             if len(request.selected_types) == 1 else "맞춤 보고서")
    return GeneratedReport(request, datetime.now(), title, tuple(sections), scope, source_label)


def _changes(frames, source):
    from setron.features.management_format import equipment_group_name
    models = {mid: group.label for group in source.catalog.groups for mid in source.bindings[group.key]}
    models.update(getattr(source, "model_names", {}))
    rows = []
    for before, after in zip(frames, frames[1:]):
        def indexed(frame):
            return {(r.equipment_id, pm.pm_name, pm.family_id, pm.group_id): (r.equipment_code, pm.version_raw)
                    for r in frame.snapshot.selected_records if not r.is_hpa for pm in r.pms}
        old, new = indexed(before), indexed(after)
        for key in sorted(old.keys() | new.keys(), key=str):
            if old.get(key) == new.get(key):
                continue
            code = (new.get(key) or old[key])[0]
            group = equipment_group_name(models.get(key[3], "설비군 미확인"))
            rows.append((str(before.day), str(after.day), code, f"{key[1] or '일반 SW'} · {group}",
                         old[key][1] or "미입력" if key in old else "이전 관측 없음",
                         new[key][1] or "미입력" if key in new else "다음 관측 없음"))
    return tuple(rows)
