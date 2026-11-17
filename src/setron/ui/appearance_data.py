"""개인 화면 설정. 업무 데이터·목표 N과 분리된 로컬 설정 계약."""

from dataclasses import asdict, dataclass, field, replace
import json
import re

from PySide6.QtCore import QSettings

from setron.features.version.groups import Tier


FONT_SCALES = {"small": 0.86, "normal": 1.0, "large": 1.15}
ACCENTS = {"Blue": "#3c5ce9", "Teal": "#078494", "Violet": "#7c4ae5"}

HEATMAP_TIERS = {
    Tier.ABOVE: ("heat_above", "N+1 이상"),
    Tier.AT: ("heat_at", "N"),
    Tier.ONE_BELOW: ("heat_one_below", "N-1"),
    Tier.TWO_BELOW: ("heat_two_below", "N-2 이하"),
    Tier.UNKNOWN: ("heat_unknown", "비교 불가 · HPA"),
}
HEATMAP_FIELDS = (*HEATMAP_TIERS.values(), ("heat_risk", "위험 후보 테두리"))
HEATMAP_KEYS = tuple(key for key, _ in HEATMAP_FIELDS)
HEATMAP_STANDARD = {
    False: ("#a9dbbd", "#ddf4e5", "#fce4e7", "#f6c5cd", "#eceef1", "#c9485e"),
    True: ("#255743", "#1d4033", "#402a33", "#60353f", "#303945", "#fa8193"),
}
HEATMAP_COLOR_VISION = {
    False: ("#85bce4", "#dbeeff", "#ffedc2", "#e6b663", "#eceef1", "#663c93"),
    True: ("#265a83", "#1b354d", "#483917", "#73511a", "#303945", "#dcacf9"),
}
DASHBOARD_FIELDS = (("met", "목표 달성"), ("below", "목표 미달"), ("dashboard_unknown", "확인 필요"),
                    ("risk", "안전·결함 위험 후보"), ("rate", "달성률 강조색"))
DASHBOARD_KEYS = tuple(key for key, _ in DASHBOARD_FIELDS)
DASHBOARD_COLOR_VISION = {
    False: ("#d8edff", "#ffe2a6", "#e9eef5", "#eadcf8", "#31569c"),
    True: ("#1b3954", "#503a17", "#303a49", "#49345d", "#9bc9ff"),
}
COLOR_FIELDS = ("accent", "canvas", "surface", *DASHBOARD_KEYS, *HEATMAP_KEYS)


@dataclass(frozen=True)
class Appearance:
    mode: str = "light"
    accent: str | None = None
    dashboard_palette: str = "standard"
    met: str | None = None
    below: str | None = None
    risk: str | None = None
    rate: str | None = None
    canvas: str | None = None
    surface: str | None = None
    # 밝은 배경 HEX가 어두운 화면까지 덮지 않도록 모드별 선택을 보관한다.
    dark_colors: dict[str, str] = field(default_factory=dict)
    font_size: str = "normal"
    dashboard_unknown: str | None = None
    heatmap_palette: str = "standard"
    heat_above: str | None = None
    heat_at: str | None = None
    heat_one_below: str | None = None
    heat_two_below: str | None = None
    heat_unknown: str | None = None
    heat_risk: str | None = None

    def __post_init__(self):
        if self.font_size not in ("small", "normal", "large"):
            raise ValueError("글자 크기는 작게, 보통, 크게 중에서 선택하세요.")
        palettes = ("standard", "distinct", "custom")
        if (self.mode not in ("light", "dark", "system") or self.dashboard_palette not in palettes
                or self.heatmap_palette not in palettes):
            raise ValueError("표시 모드 또는 색상 프리셋을 확인하세요.")
        for key in COLOR_FIELDS:
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
                raise ValueError("색상은 #RRGGBB 형식으로 입력하세요.")
        if not isinstance(self.dark_colors, dict) or any(
                key not in COLOR_FIELDS
                or not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value)
                for key, value in self.dark_colors.items()):
            raise ValueError("어두운 화면의 색상은 #RRGGBB 형식으로 입력하세요.")

    @property
    def font_scale(self):
        return FONT_SCALES[self.font_size]

    def with_colors(self, dark, **values):
        if not values:
            return self
        draft = self
        if values.keys() & set(DASHBOARD_KEYS) and self.dashboard_palette != "custom":
            draft = self._customize("dashboard_palette", DASHBOARD_KEYS)
        if values.keys() & set(HEATMAP_KEYS) and self.heatmap_palette != "custom":
            draft = draft._customize("heatmap_palette", HEATMAP_KEYS)
        return replace(draft, dark_colors={**draft.dark_colors, **values}) if dark else replace(draft, **values)

    def _customize(self, preset, keys):
        """프리셋의 다른 색은 유지하고 수정한 항목만 바꾼다."""
        light, dark = self.colors(), self.colors(True)
        dark_overrides = {key: value for key, value in self.dark_colors.items() if key not in keys}
        base = replace(self, **{preset: "custom"}, **dict.fromkeys(keys), dark_colors=dark_overrides)
        defaults, dark_defaults = base.colors(), base.colors(True)
        return replace(base, **{key: light[key] for key in keys if light[key] != defaults[key]},
                       dark_colors={**dark_overrides, **{key: dark[key] for key in keys if dark[key] != dark_defaults[key]}})

    def colors(self, dark=False):
        values = dict(ink="#e8eef9" if dark else "#142033", muted="#aab7cd" if dark else "#5b6880",
                      accent="#8ea2ff" if dark else "#3c5ce9", soft="#1b212b" if dark else "#f9fafb",
                      line="#435065" if dark else "#d5d7da", selected="#263250" if dark else "#e8ebfc",
                      canvas="#11151c" if dark else "#f5f6f8", surface="#242b36" if dark else "#ffffff",
                      met="#1d4033" if dark else "#ddf4e5", below="#443b20" if dark else "#fff0b7",
                      risk="#472d35" if dark else "#fce2e7")
        values.update(status_met=values["met"], status_below=values["below"], status_risk=values["risk"])
        override = self.dark_colors if dark else {key: getattr(self, key) for key in COLOR_FIELDS}
        for key in ("accent", "canvas", "surface"):
            if override.get(key):
                values[key] = override[key].lower()
        values.update(dashboard_unknown=values["soft"], rate=values["accent"])
        if self.dashboard_palette == "distinct":
            values.update(zip(DASHBOARD_KEYS, DASHBOARD_COLOR_VISION[dark]))
            dashboard_changed = set(DASHBOARD_KEYS)
        elif self.dashboard_palette == "custom":
            dashboard_changed = {key for key in DASHBOARD_KEYS if override.get(key)}
            values.update((key, override[key].lower()) for key in dashboard_changed)
        else:
            dashboard_changed = set()
        heatmap = HEATMAP_COLOR_VISION if self.heatmap_palette == "distinct" else HEATMAP_STANDARD
        values.update(zip(HEATMAP_KEYS, heatmap[dark]))
        if self.heatmap_palette == "custom":
            values.update((key, override[key].lower()) for key in HEATMAP_KEYS if override.get(key))
        values["dashboard_risk_marker"] = values["risk"] if "risk" in dashboard_changed else "#fa8193" if dark else "#db4054"
        values["composition_met"] = values["met"] if "met" in dashboard_changed else values["accent"]
        values["composition_met_chip"] = values["met"] if "met" in dashboard_changed else values["selected"]
        values["composition_below"] = values["below"] if "below" in dashboard_changed else "#514520" if dark else "#fdf0b8"
        unknown = values["dashboard_unknown"] if "dashboard_unknown" in dashboard_changed else None
        values["composition_unknown"] = unknown or ("#343d4d" if dark else "#e5e7eb")
        values["composition_unknown_chip"] = unknown or ("#303945" if dark else "#f3f4f6")
        values["primary"] = values["accent"] if override.get("accent") or not dark else "#a8baff"
        values["primary_hover"] = "#bdcbff" if dark and not override.get("accent") else "#3652ca"
        return values


class PersonalSettings:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings("SETRON", "Appearance")

    def _read(self, key, fallback):
        try:
            return json.loads(self.settings.value(key, "null"))
        except (ValueError, TypeError):
            return fallback

    def appearance(self):
        try:
            data = self._read("appearance", {}) or {}
            if isinstance(data, dict):
                legacy = data.pop("palette", None)
                if legacy == "distinct" and "heatmap_palette" not in data:
                    data["heatmap_palette"] = "distinct"
                if legacy is not None and "dashboard_palette" not in data:
                    dark_colors = data.get("dark_colors", {})
                    if legacy in ("standard", "distinct") and isinstance(dark_colors, dict) and any(
                            data.get(key) or dark_colors.get(key) for key in ("rate", "dashboard_unknown")):
                        if legacy == "distinct":
                            data["dark_colors"] = dark_colors
                            for key, light, dark in zip(DASHBOARD_KEYS, DASHBOARD_COLOR_VISION[False],
                                                        DASHBOARD_COLOR_VISION[True]):
                                data[key] = data.get(key) or light
                                dark_colors.setdefault(key, dark)
                        legacy = "custom"
                    data["dashboard_palette"] = legacy
            return Appearance(**data)
        except (ValueError, TypeError):
            return Appearance()

    def version_group_pair(self) -> tuple[int, int] | None:
        value = self._read("version_group_pair", None)
        if isinstance(value, list) and len(value) == 2 and all(type(key) is int and key > 0 for key in value):
            return value[0], value[1]
        return None

    def remember_version_group_pair(self, pair: tuple[int, int]):
        self.save("version_group_pair", pair)

    def save(self, key, value):
        previous = self.settings.value(key)
        self.settings.setValue(key, json.dumps(asdict(value) if isinstance(value, Appearance) else value, ensure_ascii=False))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            if previous is None:
                self.settings.remove(key)
            else:
                self.settings.setValue(key, previous)
            raise OSError("개인 설정을 저장하지 못했습니다. 이전 설정을 유지합니다.")

    def writer(self, key, value):
        """다른 스레드는 자신의 QSettings 인스턴스만 사용한다."""
        settings = self.settings
        format_, scope = settings.format(), settings.scope()
        organization, application, filename = settings.organizationName(), settings.applicationName(), settings.fileName()
        def write():
            fresh = (QSettings(format_, scope, organization, application) if organization
                     else QSettings(filename, format_))
            PersonalSettings(fresh).save(key, value)
        return write
