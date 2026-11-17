"""멘토 확정 기준 자료 — 모델명·번역표·장소표·기본값 (데이터 파트 ai/svhm/core/refdata.py 와 같은 내용).

근거: 멘토링 3차(10/21) 분류표, 회신 10/20·10/27·10/28·10/29. 세부는 ai/docs/번역표_버전맞추기_설명.md.
DB 의 equipment_group·version_family 는 database/seed_demo_catalog.sql 로 넣고, 여기서는 '현황표 글자 → 등록 모델명'과
'사이트·라인 → 메모리/파운드리 기본값'처럼 스키마에 테이블이 없는 기준만 코드로 둔다.
"""

from __future__ import annotations

import re

# 등록 모델명 → 최상위 분류 (seed_demo_catalog.sql 과 같아야 한다)
MODEL_CATEGORIES = {
    "M-C3": "POLY", "M-C4": "POLY", "M-C4 DFB": "POLY", "M-C4+": "POLY",
    "M-O2": "OXIDE", "M-O3": "OXIDE", "XMO3": "OXIDE", "M-O5": "OXIDE", "M-O5 Prime": "OXIDE",
    "Inline Ox": "KCTC", "Inline O2": "KCTC", "Cluster Ox": "KCTC", "Cluster O2": "KCTC",
    "HPA": "HPA",
}
MODEL_STRUCTURES = {"Inline Ox": "INLINE", "Inline O2": "INLINE", "Cluster Ox": "CLUSTER", "Cluster O2": "CLUSTER"}
# 개조형 → 기본형 (릴리즈노트 공유, 10/27)
MODEL_PARENTS = {"M-C4 DFB": "M-C4", "M-C4+": "M-C4", "XMO3": "M-O3", "M-O5 Prime": "M-O5"}

# 번역표: 현황표 설비군 글자(공백·대소문자 무시) → (등록 모델명, ui, 개조 유형, 형상)
# 'M-C4/C4+' 처럼 버전으로 판정하는 표기는 RESOLVE_BY_VERSION 에 둔다.
_ALIASES = {
    "M-C3 (통합UI)": ("M-C3", "INTEGRATED", None, None),
    "M-C3 (6각) (통합UI)": ("M-C3", "INTEGRATED", None, "6각"),
    "M-C3(5각) / 통합UI": ("M-C3", "INTEGRATED", None, "5각"),
    "M-C3 (구UI)": ("M-C3", "LEGACY", None, None),
    "M-C3(6각) / 구UI": ("M-C3", "LEGACY", None, "6각"),
    "M-C3": ("M-C3", None, None, None),                       # UI 미표기 → 통합UI 기본값 (10/28)
    "M-C4": ("M-C4", None, None, None),
    "M-C4 / Smart UI": ("M-C4", None, None, None),
    "M-C4(DFB)": ("M-C4 DFB", None, None, None),
    "M-C4 DFB": ("M-C4 DFB", None, None, None),
    "M-C4+ / Smart UI": ("M-C4+", None, "C4+", None),
    "M-C4+": ("M-C4+", None, "C4+", None),
    "M-O2": ("M-O2", None, None, None),
    "M-O3": ("M-O3", None, None, None),
    "XMO3": ("XMO3", None, "XMO3", None),
    "M-O5": ("M-O5", None, None, None),
    "M5": ("M-O5", None, None, None),
    "M-O5(Prime)(6각)": ("M-O5 Prime", None, "Prime", "6각"),
    "M-O5 Prime": ("M-O5 Prime", None, "Prime", None),
    "Inline Ox": ("Inline Ox", None, None, None),
    "Ox(inline)": ("Inline Ox", None, None, None),
    "Inline Ox (CIP)": ("Inline Ox", None, "CIP", None),
    "Cluster Ox": ("Cluster Ox", None, None, None),
    "Inline O2": ("Inline O2", None, None, None),
    "Cluster O2": ("Cluster O2", None, None, None),
    "5각 Cluster O2": ("Cluster O2", None, None, "5각"),
    "HPA": ("HPA", None, None, None),
}
RESOLVE_BY_VERSION = {"M-C4/C4+ / Smart UI", "M-C4/C4+"}     # 버전에 _DEX 가 있으면 M-C4+, 없으면 M-C4 (10/29)
_INDEX = {re.sub(r"\s+", "", k).casefold(): v for k, v in _ALIASES.items()}
_RESOLVE_INDEX = {re.sub(r"\s+", "", k).casefold() for k in RESOLVE_BY_VERSION}


def group_key(name: str | None) -> str:
    """설비군 이름을 표기 차이 없이 비교하는 키 (최종 멘토링 11-16: '설비군 이름 그대로').

    'C4(DFB)' · 'C4 DFB' · 'M-C4(DFB)' · 'M-C4 DFB'는 같은 설비군이다. 앞의 'M-', 공백, 괄호, '-' · '_' · '/'와
    대소문자를 무시한다. '+'처럼 뜻이 있는 글자는 남긴다 ('M-C4'와 'M-C4+'는 다르다).
    """
    text = (name or "").strip().casefold()
    text = re.sub(r"^m\s*-\s*", "", text)
    return re.sub(r"[\s()\[\]\-_/]+", "", text)


def resolve_model_text(raw: str | None, version_raw: str | None = None):
    """현황표 설비군 글자를 등록 모델명으로 푼다.

    param
    - raw: 현황표 설비군 칸의 글자. 공백·대소문자 차이는 무시한다.
    - version_raw: 'M-C4/C4+' 같은 혼합 표기를 판정할 때 참고하는 SW 버전 원문.

    return
    - (등록 모델명, ui_type, 개조 유형, 형상) 또는 번역표에 없으면 None.
    """
    key = re.sub(r"\s+", "", raw or "").casefold()
    if not key:
        return None
    if key in _RESOLVE_INDEX:
        is_plus = bool(re.search(r"[_\-]DEX\b", version_raw or "", flags=re.IGNORECASE))
        return ("M-C4+" if is_plus else "M-C4", None, "C4+" if is_plus else None, None)
    return _INDEX.get(key)


# 장소표: (사이트, 라인) → 메모리/파운드리 기본값. 같은 라인명이 두 시트에 있는 화성 NRD 는 시트명으로 가른다.
LINE_AREAS = {
    ("평택", "P1F"): "MEMORY", ("평택", "P1D"): "MEMORY", ("평택", "P2F"): "MEMORY", ("평택", "P2D"): "MEMORY",
    ("평택", "P3F"): "MEMORY", ("평택", "P3D"): "MEMORY", ("평택", "P4F"): "MEMORY",
    ("평택", "S5"): "FOUNDRY", ("평택", "V2"): "FOUNDRY", ("평택", "P3-S5"): "FOUNDRY",
    ("화성", "13L"): "MEMORY", ("화성", "15L"): "MEMORY", ("화성", "16L"): "MEMORY", ("화성", "17L"): "MEMORY",
    ("화성", "M1"): "MEMORY", ("화성", "12L"): "MEMORY",
    ("화성", "S1 (기흥)"): "FOUNDRY", ("화성", "1L (기흥)"): "FOUNDRY", ("화성", "5L (기흥)"): "FOUNDRY",
    ("화성", "S3"): "FOUNDRY", ("화성", "V1"): "FOUNDRY", ("화성", "NRD-V (V1)"): "FOUNDRY", ("화성", "MFAB"): "FOUNDRY",
}
# 파운드리용 릴리즈노트가 따로 있는 모델 (10/14 메모, 10/27 회신). 나머지는 메모리/파운드리 공통.
FOUNDRY_FAMILY_MODELS = {"M-C3", "M-C4", "M-O2"}
FOUNDRY_SUFFIX = " (파운드리)"


# 데이터 관리에서 등록한 라인의 메모리/파운드리 (11-11 6차). 장소표보다 먼저 본다. DB 관리 설정(registered_lines.v1)을
# 읽어 :func:`use_registered_lines`로 채운다 (앱 시작 · 조회 선택지 다시 읽기 · 데이터 관리에서 라인을 바꿀 때).
REGISTERED_AREAS: dict[tuple[str, str], str] = {}


def _clean_line(line) -> str:
    from setron.features.hierarchy import _clean_line as clean
    return clean(str(line))


def use_registered_lines(entries) -> None:
    """등록 라인 [{"site", "line", "fab"}]을 판정에 쓴다. 새 dict로 바꿔 끼워 다른 스레드가 읽는 중에도 안전하다."""
    global REGISTERED_AREAS
    REGISTERED_AREAS = {(str(e["site"]).strip(), _clean_line(e["line"])): e["fab"] for e in entries
                        if isinstance(e.get("fab"), str) and e["fab"]}


def default_area(site: str | None, line: str | None, sheet_name: str | None = None) -> str | None:
    """사이트·라인(·시트명)으로 메모리/파운드리 기본값을 정한다. 모르면 None.

    param
    - site, line: 현황 기준일의 사업장과 라인 원문. 라인은 공백·줄바꿈·층 표기를 정리해 비교한다.
    - sheet_name: 'Memory'/'Foundry' 처럼 영역이 적힌 시트명이면 그것을 우선한다.

    return
    - 'MEMORY' / 'FOUNDRY' / None.
    """
    if site and line:
        registered = REGISTERED_AREAS.get((site.strip(), _clean_line(line)))
        if registered:
            return registered
    name = (sheet_name or "").strip().casefold()
    if name in {"memory", "foundry"}:
        return name.upper()
    if not site or not line:
        return None
    cleaned = _clean_line(line)
    return REGISTERED_AREAS.get((site.strip(), cleaned)) or LINE_AREAS.get((site.strip(), cleaned))


def _foundry_named(text: str | None) -> bool:
    name = (text or "").casefold()
    return "foundry" in name or "파운드리" in name


def area_of(values, sheet_name: str | None = None, workbook_name: str | None = None) -> str | None:
    """한 행의 메모리/파운드리를 정한다. 저장 전 검증과 저장이 같은 판단을 쓰도록 한 곳에 둔다.

    순서: ① 행의 production_area ② 장소표(사이트·라인) ③ 시트명·파일명에 'Foundry'/'파운드리'
    (릴리즈노트는 사이트·라인 칸이 없어 파일명으로 구분한다. 예: P_PJT_M-C3_Foundry_SW_Release_List).

    param
    - values: 행의 현재 값(production_area·site·line 을 읽는다). ``get`` 이 있는 매핑.
    - sheet_name, workbook_name: 원본 시트명과 통합문서 이름. 모르면 None.

    return
    - 'MEMORY' / 'FOUNDRY' / None(판단 자료 없음 → 메모리·공통 계열을 쓴다).
    """
    from setron.features.production_area import production_area_value
    explicit = production_area_value((values.get("production_area") or "").strip())
    if explicit in {"MEMORY", "FOUNDRY"} or re.fullmatch(r"A[0-9]{9}", explicit):
        return explicit
    area = default_area(values.get("site"), values.get("line"), sheet_name)
    if area:
        return area
    if _foundry_named(sheet_name) or _foundry_named(workbook_name):
        return "FOUNDRY"
    return None


def strip_foundry(family_name: str) -> tuple[str, bool]:
    """'O2 (파운드리)' → ('O2', True). 파운드리 표시가 없으면 (원래 이름, False)."""
    if family_name.endswith(FOUNDRY_SUFFIX):
        return family_name[:-len(FOUNDRY_SUFFIX)], True
    return family_name, False
