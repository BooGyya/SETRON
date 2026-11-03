# -*- coding: utf-8 -*-
"""골든테스트 v1 — C. 번역표 적용 (C1~C13), D. 버전 맞추기 (D1~D14), E. 집계 (E1~E5)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import H
from svhm.core import refdata as R
from svhm.core.translate import resolve_group, resolve_area, resolve_family
from svhm.core.matcher import match_version, norm_version, Ladder, Rung, relative_position
from svhm.core.pipeline import match_equipment, summarize
from svhm.excel.status_parser import parse_workbook, EquipmentRow, VersionEntry

def row(group_raw, version, site="평택", sheet="P1", line="P1F", area="MEMORY", hint=None, issues=None):
    vs = [VersionEntry("", version)] if version else []
    return EquipmentRow(site, sheet, None, line, None, None, group_raw, "ESXX01", "ESXX01", None, version or "", vs, area,
                        {"sheet": sheet, "row": 0, "col_equipment": 0, "col_version": 0}, issues or [],
                        {"area_override_hint": hint} if hint else {})

def fam(res): return res.versions[0].family_name
def st(res): return res.versions[0].result.status
def rung(res): return res.versions[0].result.rung.name

# ---------------- C. 번역표
def test_C1_mc3_no_ui_is_integrated(registry):
    m = match_equipment(row("M-C3", "v3.52_22"), registry)
    assert m.group_name == "C3" and fam(m) == "C3 통합UI (메모리)" and st(m) == "EXACT"

def test_C2_c3_5gak_foundry(registry):
    m = match_equipment(row("M-C3(5각) / 통합UI", "v3.52_F15", "화성", "Foundry", "S3", "FOUNDRY"), registry)
    assert fam(m) == "C3 통합UI (파운드리)" and st(m) == "EXACT"

def test_C3_memory_version_override(registry):
    m = match_equipment(row("M-C3(6각) / 구UI", "v1.1.1.6C_10", "화성", "Foundry", "S1 (기흥)", "FOUNDRY", hint="MEMORY"), registry)
    assert m.area == "MEMORY" and "예외" in m.area_note and fam(m) == "C3 구UI (메모리)" and st(m) == "EXACT"

def test_C4_C5_c4_plus_by_version(registry):
    m = match_equipment(row("M-C4/C4+ / Smart UI", "v4.56.8.1_f1_DEX", "화성", "Foundry", "1L (기흥)", "FOUNDRY"), registry)
    assert m.group_name == "C4+" and m.versions[0].variant == "DEX" and st(m) == "EXACT" and rung(m).endswith("_DEX")
    m2 = match_equipment(row("M-C4/C4+ / Smart UI", "v4.56.7.1_f1", "화성", "Foundry", "1L (기흥)", "FOUNDRY"), registry)
    assert m2.group_name == "C4" and m2.versions[0].variant == "" and st(m2) == "EXACT"

def test_C6_inline_ox_cip():
    g = resolve_group("Inline Ox (CIP)")
    assert (g.group_name, g.modification, g.structure) == ("KCTC Inline Ox", "CIP", "INLINE")

def test_C7_C8_kctc_spellings():
    assert resolve_group("5각 Cluster O2").group_name == "KCTC Cluster O2"
    assert resolve_group("Ox(inline)").group_name == "KCTC Inline Ox"

def test_C9_o5_prime_uses_o5(registry):
    m = match_equipment(row("M-O5(Prime)(6각)", "v4.60.71.1_a", "화성", "Foundry", "MFAB", "FOUNDRY"), registry)
    assert m.group_name == "O5 Prime" and fam(m) == "O5" and st(m) == "EXACT"

def test_C10_o2_foundry_line(registry):
    m = match_equipment(row("M-O2", "v3.52_F15", "평택", "S5", "S5", "FOUNDRY"), registry)
    assert fam(m) == "O2 (파운드리)" and st(m) == "EXACT"

def test_C11_unknown_spelling(registry):
    m = match_equipment(row("MC3-INT", "v3.52_22"), registry)
    assert m.group_status == "GROUP_UNKNOWN" and m.overall == "GROUP_UNKNOWN" and "설비군을 지정" in m.messages[0]

def test_C12_hpa_excluded(registry):
    m = match_equipment(row("HPA", "", "평택", "S5", "S5", "FOUNDRY", issues=["ANALYSIS_EXCLUDED"]), registry)
    assert not m.analysis_enabled and m.overall == "EXCLUDED"

def test_C13_same_line_name_two_sheets():
    assert resolve_area("화성", "Memory", "NRD (MR2동)")[0] == "MEMORY"
    assert resolve_area("화성", "Foundry", "NRD (MR2동)")[0] == "FOUNDRY"

# ---------------- D. 버전 맞추기
def test_D1_exact(registry):
    r = match_version("v4.56.6.42", registry.get(5)); assert r.status == "EXACT" and r.rung.name == "v4.56.6.42"

def test_D2_h_marker_is_exact(registry):
    r = match_version("v3.52_13 (H)", registry.get(2)); assert r.status == "EXACT" and r.rung.name == "v3.52_13"

def test_D3_D4_whitespace_case(registry):
    assert match_version("v3.52_13_e ", registry.get(2)).status == "EXACT"
    r = match_version("V3.53_2", registry.get(7)); assert r.status == "NORMALIZED" and r.rung.name == "v3.53_2"

def test_D5_dot_underscore(registry):
    r = match_version("v999.3.7", registry.get(13)); assert r.status == "NORMALIZED" and r.rung.name == "v999.3_7"

def test_D6_dot_for_underscore_in_suffix(registry):
    r = match_version("v3.52.F14", registry.get(4)); assert r.status == "NORMALIZED" and r.rung.name == "v3.52_F14"

def test_D7_hotfix_literal(registry):
    r = match_version("v1.1.1.6B_6 + Hotfix", registry.get(1)); assert r.status == "EXACT"
    r2 = match_version("v1.1.1.6B_6+Hotfix", registry.get(1)); assert r2.status == "NORMALIZED" and r2.rung.name == "v1.1.1.6B_6 + Hotfix"

def test_D8_unregistered_hotfix_suggests_add(registry):
    r = match_version("v1.1.1.6B_6_c", registry.get(1), registry, "POLY")
    assert r.status == "UNREGISTERED" and r.add_after == "v1.1.1.6B_6" and "릴리즈노트에 추가" in r.note

def test_D9_unregistered_plain(registry):
    r = match_version("v4.56.5.9", registry.get(5), registry, "POLY")
    assert r.status == "UNREGISTERED" and r.group_suggestions == []

def test_D10_group_mismatch_suggestion(registry):
    m = match_equipment(row("Cluster Ox", "v1060", "화성", "Memory", "16L", "MEMORY"), registry)
    r = m.versions[0].result
    assert r.status == "UNREGISTERED" and (14, "v1060") in r.group_suggestions and "Cluster O2" in r.note

def test_D11_missing(registry):
    assert match_version("", registry.get(5)).status == "MISSING"

def test_D12_dfb_suffix(registry):
    r = match_version("v4.56.6.42_DFB", registry.get(5)); assert r.status == "EXACT" and r.rung.variant == "DFB"
    m = match_equipment(row("M-C4(DFB)", "v4.56.6.42_DFB"), registry)
    assert m.versions[0].variant == "DFB" and not m.versions[0].variant_mismatch

def test_D13_ambiguous():
    lad = Ladder(99, [Rung("v1.2.3", "v1.2.3", "", 10, 10), Rung("v1.2_3", "v1.2_3", "", 20, 20)])
    r = match_version("V1.2.3 ", lad)
    assert r.status == "AMBIGUOUS" and [c.name for c in r.candidates] == ["v1.2.3", "v1.2_3"]

def test_D14_manual_add(registry):
    lad = registry.get(1)
    before = len(lad.rungs)
    added = lad.add_manual("v1.1.1.6B_6_c", after="v1.1.1.6B_6 + Hotfix")
    assert added.is_manual and len(lad.rungs) == before + 1
    r = match_version("v1.1.1.6B_6_c", lad); assert r.status == "EXACT" and r.rung.is_manual
    names = [x.name for x in lad.rungs]
    assert names.index("v1.1.1.6B_6 + Hotfix") < names.index("v1.1.1.6B_6_c") < names.index("v1.1.1.6C_1")
    lad.rungs.remove(added)   # 다른 테스트 영향 없게 원복

def test_norm_version_keeps_structure():
    assert norm_version("v4.56.6.42") != norm_version("v4.56.64.2")
    assert norm_version("v999.3.7") == norm_version("v999.3_7") == norm_version(" V999_3_7 (H) ")

# ---------------- E. 집계
def test_E1_E2_applied_vs_upgrade(registry):
    lad = registry.get(5)
    n = next(r for r in lad.rungs if r.name == "v4.56.6.43")
    assert relative_position(next(r for r in lad.rungs if r.name == "v4.56.6.43_b"), n, lad) > 0
    assert relative_position(next(r for r in lad.rungs if r.name == "v4.56.6.42"), n, lad) < 0

def test_E3_rate_denominator(registry):
    rows = [row("M-C4", v) for v in ["v4.56.6.43", "v4.56.6.43_b", "v4.56.7.1_a", "v4.56.6.42", "v4.56.5.9", ""]]
    rows[-1].issues = ["VERSION_MISSING"]
    ms = [match_equipment(r, registry) for r in rows]
    s = summarize(ms, registry, {(5, ""): "v4.56.6.43"})
    assert (s.total, s.applied, s.need_upgrade, s.unregistered, s.missing) == (6, 3, 1, 1, 1)
    assert s.rate() == 50.0

def test_E4_pm_mixed_counts_both(registry):
    r = row("M-O2/O3", "", "화성", "Foundry", "NRD (MR2동)", "FOUNDRY")
    r.versions = [VersionEntry("O3", "v4.50.1.1_a"), VersionEntry("O2", "v3.53_F2")]
    m = match_equipment(r, registry)
    assert m.group_status == "MULTI_GROUP" and [v.family_name for v in m.versions] == ["O3", "O2 (파운드리)"]
    s = summarize([m], registry, {(9, ""): "v4.50.1.1_a", (8, ""): "v3.53_F1"})
    assert s.total == 2 and s.applied == 2

def test_E5_hpa_not_in_total(registry):
    ms = [match_equipment(row("HPA", "", "평택", "S5", "S5", "FOUNDRY", issues=["ANALYSIS_EXCLUDED"]), registry)]
    s = summarize(ms, registry, {})
    assert s.total == 0 and s.excluded == 1

# ---------------- 실데이터 통합
def test_integration_counts(registry):
    for path, n_rows, n_unreg in ((f"{H}/화성_설비현황표_SEMES_ETCH_교육용_2026-10-05.xlsx", 132, 3),
                                  (f"{H}/평택설비현황표_교육용_2026-10-05.xlsx", 236, 3)):
        res = parse_workbook(path)
        ms = [match_equipment(r, registry) for s in res["results"] for r in s.rows]
        assert len(ms) == n_rows
        assert sum(1 for m in ms if m.overall == "UNREGISTERED") == n_unreg
        assert all(m.group_status in ("OK", "MULTI_GROUP") for m in ms)
