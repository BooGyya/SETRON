"""원본 모델 표기를 등록 모델로 바꾸는 '번역표 후보' (데이터 파트 번역표 연동).

출처: 데이터 파트 `ai/svhm/core/refdata.py`의 ALIASES(원문 모델명 → 설비군·UI·구조·개조)를 지금 시연용 시드
(`database/seed_demo_catalog.sql`, 9계열·메모리/파운드리 미분리)의 모델 이름으로 옮겼다.

후보로만 쓴다 (팀 결정 2026-11-03).
- 검토 화면에서 사용자가 '번역표 후보 적용'을 눌러야 값이 바뀌고, 바뀐 값은 사용자 수정으로 기록된다.
- 이미 입력된 값은 덮어쓰지 않는다. 모델명만 등록 모델 이름으로 바꾸고 나머지는 빈 칸만 채운다.
- 시드에 모델이 없는 표기(KCTC Inline Ox·Cluster Ox)는 후보를 내지 않는다.
- C3에서 UI 표기가 없는 경우의 '통합UI'는 데이터 파트 번역표의 값이며, 멘토링 확정 사항에는 없다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

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


def _c3(raw, ui, note=None):
    return ModelCandidate(raw, "M-C3", POLY, "C3", ui_type=ui, note=note)


def _c4(raw, modification=None, note=None):
    return ModelCandidate(raw, "M-C4", POLY, "C4", modification_type=modification, note=note)


NO_DFB_MODEL = "시드에 C4 DFB 모델이 없어 M-C4로 둠 (DFB는 같은 계열의 _DFB 버전)"
NO_C4_PLUS_MODEL = "시드에 C4+ 모델이 없어 M-C4로 둠"

CANDIDATES: tuple[ModelCandidate, ...] = (
    _c3("M-C3 (통합UI)", INTEGRATED),
    _c3("M-C3 (6각) (통합UI)", INTEGRATED),
    _c3("M-C3(5각) / 통합UI", INTEGRATED),
    _c3("M-C3 (구UI)", LEGACY),
    _c3("M-C3(6각) / 구UI", LEGACY),
    _c3("M-C3", INTEGRATED, "UI 표기가 없어 데이터 파트 번역표대로 통합UI로 둠 (확정 사항 아님, 원본 확인 필요)"),
    _c4("M-C4"),
    _c4("M-C4 / Smart UI"),
    _c4("M-C4/C4+ / Smart UI", note="C4와 C4+가 섞인 표기. 시드에 C4+ 모델이 없어 M-C4로 둠"),
    _c4("M-C4(DFB)", note=NO_DFB_MODEL),
    _c4("M-C4 DFB", note=NO_DFB_MODEL),
    _c4("M-C4+ / Smart UI", "C4+", NO_C4_PLUS_MODEL),
    _c4("M-C4+", "C4+", NO_C4_PLUS_MODEL),
    ModelCandidate("M-O2", "M-O2", OXIDE, "O2"),
    ModelCandidate("M-O3", "M-O3", OXIDE, "O3"),
    ModelCandidate("XMO3", "M-O3", OXIDE, "O3", modification_type="XMO3", note="시드에 XMO3 모델이 없어 M-O3로 둠"),
    ModelCandidate("M-O5", "M-O5", OXIDE, "O5"),
    ModelCandidate("M-O5(Prime)(6각)", "M-O5 Prime", OXIDE, "O5"),
    ModelCandidate("M-O5 Prime", "M-O5 Prime", OXIDE, "O5"),
    ModelCandidate("Inline O2", "Inline O2", KCTC, "Inline O2", structure_type="INLINE"),
    ModelCandidate("Cluster O2", "Cluster O2", KCTC, "Cluster O2", structure_type="CLUSTER"),
    ModelCandidate("5각 Cluster O2", "Cluster O2", KCTC, "Cluster O2", structure_type="CLUSTER"),
    ModelCandidate("HPA", "HPA", HPA, "HPA"),
)


def _key(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


_INDEX = {_key(c.raw): c for c in CANDIDATES}


def find_candidate(raw_model: str | None) -> ModelCandidate | None:
    """원문 모델 표기(공백·대소문자 무시)에 맞는 후보. 없으면 None."""
    if not isinstance(raw_model, str) or not raw_model.strip():
        return None
    return _INDEX.get(_key(raw_model))


def candidate_changes(current: Mapping[str, str | None], fields) -> dict[str, str]:
    """검토 행의 현재 값에 후보를 적용할 때 바뀌는 항목만 돌려준다.

    모델명은 등록 모델 이름과 다르면 바꾸고, 나머지 항목은 비어 있을 때만 채운다. ``fields``에 없는 항목은 넣지 않는다.
    """
    candidate = find_candidate(current.get("model_name"))
    if candidate is None:
        return {}
    changes = {}
    for name, value in candidate.values().items():
        if name not in fields:
            continue
        existing = current.get(name)
        if name == "model_name":
            if existing != value:
                changes[name] = value
        elif existing is None or not str(existing).strip():
            changes[name] = value
    return changes
