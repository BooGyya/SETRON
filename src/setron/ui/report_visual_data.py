"""보고서의 설비군·원문 라인 집계. 화면과 같은 고정 관측·PM·목표 판정을 사용한다."""

from collections import defaultdict
from dataclasses import dataclass, replace
import re

from setron.features.aggregation.service import summarize
from setron.features.management_format import equipment_group_name
from setron.features.risk.schemas import RiskStatus
from setron.features.version.groups import Tier
from setron.ui.heatmap_data import record_tiles


@dataclass(frozen=True)
class ReportHeatmapCell:
    code: str
    tier: Tier
    risk: RiskStatus
    risk_detail: str = ""


@dataclass(frozen=True)
class ReportHeatmap:
    area: str
    site: str
    group: str
    lines: tuple[tuple[str, tuple[ReportHeatmapCell, ...]], ...]


@dataclass(frozen=True)
class ReportLineTrend:
    area: str
    site: str
    group: str
    days: tuple
    lines: tuple[str, ...]
    rates: tuple[tuple[float | None, ...], ...]  # 라인별. 미관측·분모 없음은 None.


def natural(value):
    return tuple((0, int(p)) if p.isdigit() else (1, p.casefold())
                 for p in re.split(r"(\d+)", value) if p)


def _position(source, record):
    location = source.location_for(record)
    areas = {o.key: o.label for o in source.catalog.fabs}
    sites = {o.key: o.label for o in source.catalog.sites}
    lines = {o.key: o.label for o in source.catalog.lines}
    # 명시된 라인만 사용한다. P1F/P1D·P4H/P4D·P3-S5를 줄이지 않는다.
    return (areas.get(location.fab_key, "분류 미지정"), sites.get(location.site_key, record.site),
            lines.get(location.line_key, "라인 미지정"))


def _frame_groups(source, frame):
    """상단 C4 필터가 DFB까지 묶어도 보고서는 실제 PM 모델로 나눠 같은 서비스로 비교한다."""
    result = defaultdict(list)
    binding_names = {mid: group.label for group in source.catalog.groups for mid in source.bindings[group.key]}
    model_names = getattr(source, "model_names", {})
    for record in frame.snapshot.selected_records:
        if record.is_hpa:
            continue
        pms = defaultdict(list)
        for pm in record.pms:
            model = model_names.get(pm.group_id)
            if model is None and len({p.group_id for p in record.pms}) == 1 and equipment_group_name(record.model_name) == "DFB":
                model = record.model_name
            name = equipment_group_name(model or binding_names.get(pm.group_id, record.model_name or "설비군 미확인"))
            pms[name].append(pm)
        if not pms:
            pms[equipment_group_name(record.model_name or "설비군 미확인")] = []
        for name, members in pms.items():
            area, site, line = _position(source, record)
            result[(area, site, name, line)].append(replace(record, pms=tuple(members)))
    return result


def report_visuals(source, frames, current):
    """분류·사업장·설비군별 그래프와 같은 키의 현재 히트맵·수치표를 만든다."""
    frames = tuple(frames)
    groups_by_day = [_frame_groups(source, frame) for frame in frames]
    keys = {key for groups in groups_by_day for key in groups}
    latest = _frame_groups(source, current) if current is not None else {}
    keys.update(latest)
    ordered = sorted(keys, key=lambda k: (k[0] != "Memory", k[0] != "Foundry", *(natural(x) for x in k)))
    panels = dict.fromkeys(key[:3] for key in ordered)
    trends, heatmaps, counts, risks = [], [], [], []
    for panel in panels:
        lines = tuple(key[3] for key in ordered if key[:3] == panel)
        rates = []
        heatmap_lines = []
        for line in lines:
            key = (*panel, line)
            values = []
            for frame, groups in zip(frames, groups_by_day):
                entries = groups.get(key, ())
                summary = summarize(tuple(entries), source.families, frame.snapshot.effective_targets)
                values.append(summary.rate if entries else None)
            rates.append(tuple(values))
            entries = latest.get(key, ())
            if not entries:
                continue
            records = tuple(entries)
            summary = summarize(records, source.families, current.snapshot.effective_targets)
            tiles = record_tiles(records, summary, source)
            cells = tuple(ReportHeatmapCell(t.code, t.tier, t.risk, t.risk_detail) for t in tiles)
            heatmap_lines.append((line, cells))
            counts.append((*panel, line, len({r.equipment_id for r in records}), summary.compliant,
                           summary.below, summary.undetermined, summary.rate,
                           sum(t.risk is RiskStatus.RISK for t in tiles)))
            for tile in tiles:
                if tile.risk is RiskStatus.RISK:
                    risks.append((*panel, line, tile.code, tile.risk_label, tile.risk_detail))
        trends.append(ReportLineTrend(*panel, tuple(f.day for f in frames), lines, tuple(rates)))
        if heatmap_lines:
            heatmaps.append(ReportHeatmap(*panel, tuple(heatmap_lines)))
    return tuple(trends), tuple(heatmaps), tuple(counts), tuple(risks)


def hpa_rows(frame, source, query, base_date):
    """HPA는 목표 달성률에서 제외하고 원문 관측 목록으로만 보존한다."""
    records = (frame.snapshot.selected_records if frame else
               source._scope_records(source.snapshot_records(base_date), query))
    rows = []
    for record in records:
        if record.is_hpa:
            area, site, line = _position(source, record)
            rows.append((site, area, line, record.equipment_code, str(record.base_date),
                         " / ".join(f"{p.pm_name or '공통'} · {p.version_raw or '미입력'}" for p in record.pms),
                         "목표·위험 분석 제외"))
    return tuple(rows)
