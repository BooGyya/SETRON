"""원본 모델 표기를 등록 모델로 바꾸는 '번역표 후보' (데이터 파트 번역표 연동).

출처: 데이터 파트 번역표(`features/reference_data.py` = `ai/svhm/core/refdata.py`)를 시드 v2
(`database/seed_demo_catalog.sql`, 모델 14·계열 15, 파운드리 계열 분리)의 모델 이름으로 옮겼다.

후보로만 쓴다 (팀 결정 2026-11-03).
- 검토 화면에서 사용자가 '번역표 후보 적용'을 눌러야 값이 바뀌고, 바뀐 값은 사용자 수정으로 기록된다.
- 이미 입력된 값은 덮어쓰지 않는다. 모델명만 등록 모델 이름으로 바꾸고 나머지는 빈 칸만 채운다.
- KCTC Inline Ox·Cluster Ox, C4 DFB·C4+·XMO3 는 시드 v2에 모델이 있어 후보를 낸다 (멘토 확정 2026-10-29 KCTC 4개 분리).
- C3에서 UI 표기가 없는 경우의 '통합UI'는 멘토 확정(2026-10-28 회신)이다.
- 'M-C4/C4+' 는 같은 행의 SW 버전에 _DEX 가 있으면 M-C4+, 없으면 M-C4 (멘토 확정 2026-10-29).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from setron.features import reference_data as _reference

POLY, OXIDE, KCTC, HPA = "Poly", "Oxide", "KCTC", "HPA"
INTEGRATED, LEGACY = "통합UI", "구UI"


@dataclass(frozen=True)
class ModelCandidate:
    """원문 모델 표기 하나의 후보 값. None인 항목은 후보가 없다."""

    raw: str
    model_name: str
    group_name: str
    family_name: str
    ui_type: str | None = None
    structure_type: str | None = None
    modification_type: str | None = None
    note: str | None = None

    def values(self) -> dict[str, str]:
        """검토 행에 넣을 항목과 값."""
        fields = ("model_name", "group_name", "family_name", "ui_type", "structure_type", "modification_type")
        return {name: getattr(self, name) for name in fields if getattr(self, name) is not None}


_CATEGORY_LABEL = {"POLY": POLY, "OXIDE": OXIDE, "KCTC": KCTC, "HPA": HPA}
_FAMILY_OF = {"M-C3": "C3", "M-C4": "C4", "M-C4 DFB": "C4", "M-C4+": "C4", "M-O2": "O2", "M-O3": "O3", "XMO3": "O3",
              "M-O5": "O5", "M-O5 Prime": "O5", "Inline Ox": "Inline Ox", "Inline O2": "Inline O2",
              "Cluster Ox": "Cluster Ox", "Cluster O2": "Cluster O2", "HPA": "HPA"}
_NOTES = {
    "M-C3": "UI 표기가 없어 통합UI로 둠 (멘토 확정 2026-10-28: 미표기 M-C3는 통합UI, 구UI가 특별한 설비)",
    "C4": "계열 이름만 적힌 표기(릴리즈노트 모델 칸 등). 데이터 파트 설비군 이름 C4를 등록 모델 M-C4로 둠",
    "M-C4/C4+ / Smart UI": "C4와 C4+가 섞인 표기. SW 버전에 _DEX가 있으면 M-C4+, 없으면 M-C4 (멘토 확정 2026-10-29)",
    "M-C4/C4+": "C4와 C4+가 섞인 표기. SW 버전에 _DEX가 있으면 M-C4+, 없으면 M-C4 (멘토 확정 2026-10-29)",
    "XMO3": "O3 개조형. 릴리즈노트는 O3와 같음 (멘토 확정 2026-10-27)",
    "M-C4(DFB)": "C4 DFB 개조형. 릴리즈노트는 C4와 같고 _DFB 버전 줄기를 씀 (멘토 확정 2026-10-21)",
    "M-C4 DFB": "C4 DFB 개조형. 릴리즈노트는 C4와 같고 _DFB 버전 줄기를 씀 (멘토 확정 2026-10-21)",
    "M-C4+ / Smart UI": "C4+ 개조형. 릴리즈노트는 C4와 같고 _DEX 버전 줄기를 씀 (멘토 확정 2026-10-29)",
    "M-C4+": "C4+ 개조형. 릴리즈노트는 C4와 같고 _DEX 버전 줄기를 씀 (멘토 확정 2026-10-29)",
    "Inline Ox (CIP)": "CIP 개조형. 릴리즈노트는 Inline Ox와 같음 (멘토 확정 2026-10-21)",
}
KCTC_NOTE = "현황표에 Oxide 묶음으로 적힌 KCTC 설비를 KCTC로 둠 (최상위 설비군 Poly/Oxide/KCTC, 멘토 확정)"


def _build_candidates() -> tuple[ModelCandidate, ...]:
    """기준 자료(reference_data)의 번역표에서 후보 목록을 만든다. 'M-C4/C4+' 는 버전이 필요해 별도 처리."""
    out = []
    for raw, (model, ui, modification, _geometry) in _reference._ALIASES.items():
        ui_label = {"INTEGRATED": INTEGRATED, "LEGACY": LEGACY}.get(ui) if ui else (INTEGRATED if model == "M-C3" else None)
        out.append(ModelCandidate(raw, model, _CATEGORY_LABEL[_reference.MODEL_CATEGORIES[model]], _FAMILY_OF[model],
                                  ui_type=ui_label, structure_type=_reference.MODEL_STRUCTURES.get(model),
                                  modification_type=modification, note=_NOTES.get(raw)))
    out.append(ModelCandidate("C4", "M-C4", POLY, "C4", note=_NOTES["C4"]))
    for raw in sorted(_reference.RESOLVE_BY_VERSION):
        out.append(ModelCandidate(raw, "M-C4", POLY, "C4", note=_NOTES[raw]))
    return tuple(out)


CANDIDATES: tuple[ModelCandidate, ...] = _build_candidates()


def _key(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


_INDEX = {_key(c.raw): c for c in CANDIDATES}
_VERSION_KEYS = {_key(r) for r in _reference.RESOLVE_BY_VERSION}


def find_candidate(raw_model: str | None) -> ModelCandidate | None:
    """원문 모델 표기(공백·대소문자 무시)에 맞는 후보. 없으면 None."""
    if not isinstance(raw_model, str) or not raw_model.strip():
        return None
    return _INDEX.get(_key(raw_model))


def candidate_changes(current: Mapping[str, str | None], fields) -> dict[str, str]:
    """검토 행의 현재 값에 후보를 적용할 때 바뀌는 항목만 돌려준다.

    모델명은 등록 모델 이름과 다르면 바꾸고, 나머지 항목은 비어 있을 때만 채운다. ``fields``에 없는 항목은 넣지 않는다.
    'M-C4/C4+' 표기는 같은 행의 SW 버전(_DEX)으로 C4+/C4를 정한다.
    """
    candidate = find_candidate(current.get("model_name"))
    if candidate is None:
        return {}
    if _key(candidate.raw) in _VERSION_KEYS:
        resolved = _reference.resolve_model_text(candidate.raw, current.get("version_raw"))
        if resolved is not None:
            candidate = ModelCandidate(candidate.raw, resolved[0], POLY, "C4", modification_type=resolved[2], note=candidate.note)
    changes = {}
    for name, value in candidate.values().items():
        if name not in fields:
            continue
        existing = current.get(name)
        if name == "model_name":
            if existing != value:
                changes[name] = value
        elif (name == "group_name" and candidate.group_name == KCTC
              and str(existing or "").strip().casefold() == "oxide"):
            # 현황표는 KCTC 설비를 Oxide 묶음 아래 적는다. 최상위 분류는 KCTC (멘토 확정: Poly/Oxide/KCTC 3개).
            changes[name] = value
        elif existing is None or not str(existing).strip():
            changes[name] = value
    return changes
