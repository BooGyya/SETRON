from setron.features.hierarchy import legacy_line_parts, line_parts


def test_explicit_parent_and_root_override_legacy_name_rules():
    entries = [{"site": "평택", "line": "P1D", "fab": "MEMORY", "parent_line": None},
               {"site": "평택", "line": "임의 이름", "fab": "MEMORY", "parent_line": "다른 Line"}]
    assert line_parts("평택", "P1D", entries) == ("P1D", None)
    assert line_parts("평택", "임의 이름", entries) == ("다른 Line", "임의 이름")
    assert line_parts("다른 Site", "P1D", entries) == ("P1", "P1D")
    assert legacy_line_parts("P4H(3F)") == ("P4", "P4H")
    assert legacy_line_parts(None) == (None, None)
