"""개인 화면 설정. 업무 데이터·목표 N과 분리된 로컬 설정 계약."""

from dataclasses import asdict, dataclass, field, replace
import json
import re

from PySide6.QtCore import QSettings


MENU_ORDER = ("dashboard", "heatmap", "items", "ai", "import", "replay")
ACCENTS = {"Blue": "#3c5ce9", "Teal": "#078494", "Violet": "#7c4ae5"}


@dataclass(frozen=True)
class Appearance:
    mode: str = "light"
    accent: str | None = None
    palette: str = "standard"
    met: str | None = None
    below: str | None = None
    risk: str | None = None
    rate: str | None = None
    canvas: str | None = None
    surface: str | None = None
    # 밝은 배경 HEX가 어두운 화면까지 덮지 않도록 모드별 선택을 보관한다.
    dark_colors: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.mode not in ("light", "dark", "system") or self.palette not in ("standard", "distinct", "custom"):
            raise ValueError("표시 모드 또는 히트맵 색상 선택을 확인하세요.")
        for key in ("accent", "met", "below", "risk", "rate", "canvas", "surface"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
                raise ValueError("색상은 #RRGGBB 형식으로 입력하세요.")
        if not isinstance(self.dark_colors, dict) or any(
                key not in ("accent", "met", "below", "risk", "rate", "canvas", "surface")
                or not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value)
                for key, value in self.dark_colors.items()):
            raise ValueError("어두운 화면의 색상은 #RRGGBB 형식으로 입력하세요.")

    def with_colors(self, dark, **values):
        return replace(self, dark_colors={**self.dark_colors, **values}) if dark else replace(self, **values)

    def colors(self, dark=False):
        values = dict(ink="#e8eef9" if dark else "#142033", muted="#aab7cd" if dark else "#5b6880",
                      accent="#8ea2ff" if dark else "#3c5ce9", soft="#1b212b" if dark else "#f9fafb",
                      line="#435065" if dark else "#d5d7da", selected="#263250" if dark else "#e8ebfc",
                      canvas="#11151c" if dark else "#f5f6f8", surface="#242b36" if dark else "#ffffff",
                      met="#1d4033" if dark else "#ddf4e5", below="#443b20" if dark else "#fff0b7",
                      risk="#472d35" if dark else "#ffe1e5")
        if self.palette == "distinct":
            values.update(met="#153f50" if dark else "#d8f1fa", below="#493714" if dark else "#ffe2a6",
                          risk="#492e49" if dark else "#f4dbf2")
        override = self.dark_colors if dark else {key: getattr(self, key) for key in
                    ("accent", "met", "below", "risk", "rate", "canvas", "surface")}
        for key in ("accent", "canvas", "surface"):
            if override.get(key):
                values[key] = override[key].lower()
        if self.palette == "custom":
            for key in ("met", "below", "risk"):
                if override.get(key):
                    values[key] = override[key].lower()
        values["rate"] = (override.get("rate") or values["accent"]).lower()
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
            return Appearance(**(self._read("appearance", {}) or {}))
        except (ValueError, TypeError):
            return Appearance()

    def personal(self):
        data = self._read("personal", {})
        data = data if isinstance(data, dict) else {}
        order = data.get("order", MENU_ORDER)
        if not isinstance(order, (list, tuple)) or len(order) != len(MENU_ORDER) or set(order) != set(MENU_ORDER):
            order = MENU_ORDER
        return {"order": tuple(order), "home": "heatmap" if data.get("home") == "heatmap" else "metrics"}

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
