"""히트맵의 표시 기준만 바꾸는 공통 그룹 계획. 저장값과 판정은 변경하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
import re

from setron.ui.heatmap_data import EquipmentTile, GoalState


GROUPING_MODES = (
    ("site", "사업장"),
    ("fab", "분류"),
    ("line", "라인"),
    ("subline", "세부라인"),
    ("group", "설비군"),
)


@dataclass(frozen=True)
class HeatmapGroupNode:
    key: tuple[str, ...]
    title: str
    tiles: tuple[EquipmentTile, ...]
    children: tuple[HeatmapGroupNode, ...] = ()
    group_name: str | None = None

    @property
    def equipment_count(self):
        return len({tile.equipment_id for tile in self.tiles})


def group_families(tile, group_name=None):
    """카드와 목록이 동일한 설비군 SW 원문을 표시한다."""
    return tuple(family for family in tile.families
                 if group_name is None or group_name in family.title.split(" · ")[0].split(" / ")) or tile.families


def split_line(value: str | None) -> tuple[str | None, str | None]:
    """표시용 Line과 세부 Line을 나눈다. 원래 타일의 Line 원문은 그대로 둔다."""
    if not value or not value.strip():
        return None, None
    cleaned = re.sub(r"\(\s*\d+\s*[Ff]?\s*\)", "", value)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None, None
    match = re.fullmatch(r"(P\d+)\s*([FD])", cleaned, flags=re.IGNORECASE)
    if match:
        line, suffix = (part.upper() for part in match.groups())
        return line, line + suffix
    if re.fullmatch(r"P4\s*H", cleaned, flags=re.IGNORECASE):
        return "P4", "P4H"
    return cleaned, None


@dataclass(frozen=True)
class _Position:
    site_key: str
    site_label: str
    fab_key: str
    fab_label: str | None
    line: str | None
    subline: str | None

    @property
    def line_key(self):
        return self.site_key, self.fab_key, self.line or ""

    @property
    def subline_key(self):
        return *self.line_key, self.subline or ""


def _position(tile):
    raw_fab = (tile.fab_label or "").strip()
    fab_key = raw_fab.upper() if raw_fab.casefold() in ("memory", "foundry") else raw_fab
    fab_label = {"MEMORY": "Memory", "FOUNDRY": "Foundry"}.get(fab_key, raw_fab or None)
    line, subline = split_line(tile.line_label or tile.area)
    return _Position(tile.site_key or "", tile.site_label or "Site 미지정", fab_key, fab_label, line, subline)


def _natural(value):
    return tuple(int(part) if part.isdigit() else part.casefold()
                 for part in re.split(r"(\d+)", value or ""))


def _fab_order(key):
    return ({"MEMORY": 0, "FOUNDRY": 1, "": 3}.get(key, 2), _natural(key))


def _unique(tiles):
    found = {}
    for tile in tiles:
        found.setdefault(tile.equipment_id, tile)
    return tuple(found.values())


def _location_title(position, line):
    return line or "미지정"


def build_heatmap_groups(tiles, groups, *, mode="group", site_order=(), show_hpa=False):
    """고유 타일과 모델별 설비 ID로 재사용 가능한 그룹 트리를 만든다.

    groups는 (설비군 키, 표시 이름, 설비 ID 모음) 순서다. 같은 설비가 여러
    설비군에 표시되어도 각 상위 노드의 대수는 물리 설비 ID로 계산한다.
    """
    if mode not in dict(GROUPING_MODES):
        raise ValueError("지원하지 않는 히트맵 그룹 기준입니다.")
    tiles = _unique(tiles)
    models = tuple((str(key), name, frozenset(ids)) for key, name, ids in groups)
    positions = {tile.equipment_id: _position(tile) for tile in tiles}
    sites = {key: index for index, key in enumerate(site_order)}
    analyzed = tuple(tile for tile in tiles if tile.goal is not GoalState.EXCLUDED)

    def location_order(position, *, detail=False):
        return (sites.get(position.site_key, sites.get(position.site_label, len(sites))),
                _natural(position.site_label), _fab_order(position.fab_key),
                position.line is None, _natural(position.line),
                position.subline is None if detail else False,
                _natural(position.subline) if detail else ())

    def model_nodes(members, prefix, *, locations=False):
        nodes, covered = [], set()
        for key, name, ids in models:
            selected = tuple(tile for tile in members if tile.equipment_id in ids)
            if selected:
                covered.update(tile.equipment_id for tile in selected)
                node_key = (*prefix, "group", key)
                nodes.append(HeatmapGroupNode(node_key, name, selected,
                                             line_nodes(selected, node_key) if locations else (), name))
        unknown = tuple(tile for tile in members if tile.equipment_id not in covered)
        if unknown:
            node_key = (*prefix, "group", "__unassigned__")
            nodes.append(HeatmapGroupNode(node_key, "설비군 미확인", unknown,
                                         line_nodes(unknown, node_key) if locations else ()))
        return tuple(nodes)

    def line_nodes(members, prefix, *, models_below=False):
        buckets = {}
        for tile in members:
            buckets.setdefault(positions[tile.equipment_id].line_key, []).append(tile)
        nodes = []
        ordered = sorted(buckets.items(), key=lambda item: location_order(positions[item[1][0].equipment_id]))
        for key, bucket in ordered:
            selected = tuple(bucket)
            position = positions[selected[0].equipment_id]
            node_key = (*prefix, "line", *key)
            if any(positions[tile.equipment_id].subline is not None for tile in selected):
                details = {}
                for tile in selected:
                    details.setdefault(positions[tile.equipment_id].subline, []).append(tile)
                children = []
                for detail, detail_tiles in sorted(details.items(), key=lambda item: (item[0] is None, _natural(item[0]))):
                    child_key = (*node_key, "subline", detail or "")
                    title = detail or "미지정"
                    entries = tuple(detail_tiles)
                    children.append(HeatmapGroupNode(child_key, title, entries,
                                                     model_nodes(entries, child_key) if models_below else ()))
                children = tuple(children)
            else:
                children = model_nodes(selected, node_key) if models_below else ()
            nodes.append(HeatmapGroupNode(node_key, _location_title(position, position.line), selected, children))
        return tuple(nodes)

    def fab_nodes(members, prefix):
        buckets = {}
        for tile in members:
            buckets.setdefault(positions[tile.equipment_id].fab_key, []).append(tile)
        nodes = []
        for key, bucket in sorted(buckets.items(), key=lambda item: _fab_order(item[0])):
            selected = tuple(bucket)
            node_key = (*prefix, "fab", key)
            title = positions[selected[0].equipment_id].fab_label or "미지정"
            nodes.append(HeatmapGroupNode(node_key, title, selected,
                                         line_nodes(selected, node_key, models_below=True)))
        return tuple(nodes)

    if mode == "group":
        result = model_nodes(analyzed, (), locations=True)
    elif mode == "site":
        buckets = {}
        for tile in analyzed:
            # 확인된 키를 우선하고, 키가 없으면 원문 이름으로만 구분한다.
            buckets.setdefault(tile.site_key or tile.site_label or "", []).append(tile)
        nodes = []
        ordered = sorted(buckets.items(), key=lambda item: (
            sites.get(item[0], sites.get(item[1][0].site_label, len(sites))),
            _natural(item[1][0].site_label or item[0]), _natural(item[0])))
        for key, bucket in ordered:
            selected = tuple(bucket)
            node_key = ("site", key)
            title = selected[0].site_label or selected[0].site_key or "미지정"
            nodes.append(HeatmapGroupNode(node_key, title, selected, fab_nodes(selected, node_key)))
        result = tuple(nodes)
    elif mode == "line":
        result = line_nodes(analyzed, (), models_below=True)
    elif mode == "subline":
        buckets = {}
        for tile in analyzed:
            buckets.setdefault(positions[tile.equipment_id].subline_key, []).append(tile)
        nodes = []
        ordered = sorted(buckets.items(), key=lambda item: location_order(positions[item[1][0].equipment_id], detail=True))
        for key, bucket in ordered:
            selected = tuple(bucket)
            position = positions[selected[0].equipment_id]
            node_key = ("subline", *key)
            nodes.append(HeatmapGroupNode(node_key, _location_title(position, position.subline or position.line),
                                         selected, model_nodes(selected, node_key)))
        result = tuple(nodes)
    else:
        result = fab_nodes(analyzed, ())

    hpa = tuple(tile for tile in tiles if tile.goal is GoalState.EXCLUDED)
    if show_hpa and hpa:
        result += (HeatmapGroupNode(("hpa",), "HPA · 분석 제외", hpa, line_nodes(hpa, ("hpa",))),)
    return result
