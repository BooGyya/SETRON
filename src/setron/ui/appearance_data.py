"""개인 화면 설정. 업무 데이터·목표 N과 분리된 로컬 설정 계약."""

from dataclasses import asdict, dataclass
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

    def __post_init__(self):
        if self.mode not in ("light", "dark", "system") or self.palette not in ("standard", "distinct", "custom"):
            raise ValueError("표시 모드 또는 히트맵 색상 선택을 확인하세요.")
        for key in ("accent", "met", "below", "risk", "rate", "canvas", "surface"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
                raise ValueError("색상은 #RRGGBB 형식으로 입력하세요.")

    def colors(self, dark=False):
        values = dict(ink="#e8eef9" if dark else "#142033", muted="#aab7cd" if dark else "#5b6880",
                      accent="#8ea2ff" if dark else "#3c5ce9", soft="#1b212b" if dark else "#f9fafb",
                      line="#435065" if dark else "#d5d7da", selected="#26344b" if dark else "#e8ebfc",
                      canvas="#0d1422" if dark else "#f5f6f8", surface="#242b36" if dark else "#ffffff",
                      met="#1d4033" if dark else "#ddf4e5", below="#443b20" if dark else "#fff0b7",
                      risk="#472d35" if dark else "#ffe1e5")
        if self.palette == "distinct":
            values.update(met="#153f50" if dark else "#d8f1fa", below="#493714" if dark else "#ffe2a6",
                          risk="#492e49" if dark else "#f4dbf2")
        for key in ("accent", "canvas", "surface"):
            if getattr(self, key):
                values[key] = getattr(self, key).lower()
        if self.palette == "custom":
            for key in ("met", "below", "risk"):
                if getattr(self, key):
                    values[key] = getattr(self, key).lower()
        values["rate"] = self.rate or values["accent"]
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
        return {"order": tuple(order), "home": "heatmap" if data.get("home") == "heatmap" else "metrics",
                "lines": data.get("lines") if isinstance(data.get("lines"), list) else None,
                "groups": data.get("groups") if isinstance(data.get("groups"), list) else None}

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
