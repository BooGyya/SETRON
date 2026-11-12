"""시연 시드의 목표 N (11/09 팀 결정 A): 모델·계열마다 '최신 바로 아래 버전'을 N 그룹으로 저장한다.

seed.sql은 ID를 SQL 변수(@m·@f·@r)로 잇기 때문에, 변수에 임의 숫자를 넣어 앱의 N 그룹 형식 검사를 그대로 통과하는지 본다.
"""

import json
import re
import sys
from pathlib import Path

import pytest

from setron.features.targets.groups import LEGACY_SCHEMA, _current, setting_key
from setron.features.version.groups import VersionGroups

DATABASE = Path(__file__).resolve().parents[1] / "database"


@pytest.fixture(scope="module")
def built():
    sys.path.insert(0, str(DATABASE))
    try:
        import build_seed
        from seed_source import candidates
        from seed_augmentation import prepare_seed
        catalog = json.loads((DATABASE / "demo_release_catalog.json").read_text(encoding="utf-8"))
        effective_catalog, _ = prepare_seed(catalog)
        return build_seed, candidates, effective_catalog, build_seed.build_sql(catalog)
    finally:
        sys.path.remove(str(DATABASE))


def _evaluate(expr, env):
    expr = re.sub(r"CONVERT\(0x([0-9a-f]*) USING utf8mb4\) COLLATE utf8mb4_unicode_ci",
                  lambda m: json.dumps(bytes.fromhex(m.group(1)).decode()), expr)
    expr = re.sub(r"@(\w+)", lambda m: str(env.setdefault(m.group(1), 1000 + len(env))), expr)
    return eval(expr.replace("CONCAT(", "concat("), {"concat": lambda *parts: "".join(str(p) for p in parts)})


def _split(body):
    depth = 0
    for i, ch in enumerate(body):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            return body[:i].strip(), body[i + 1:].strip()
    raise AssertionError("값 두 개를 찾지 못했습니다")


def test_every_pair_with_release_notes_gets_one_n_group_in_app_format(built):
    _, _, catalog, sql = built
    statements = [s for s in sql.split(";\n\n")
                  if s.startswith("INSERT INTO app_setting") and "SELECT CONCAT('version_groups.v1:'" in s]
    build_seed, candidates = built[0], built[1]
    pairs = {(t["model"], t["family"]) for t in catalog["templates"]
             if t["model"] != "HPA" and build_seed.demo_target(catalog, candidates, t["model"], t["family"]) is not None}
    assert len(statements) == len(pairs) == 18              # 원본 또는 합성 시연 근거가 있는 모든 비HPA 쌍
    for statement in statements:
        env = {}
        key_sql, value_sql = _split(statement.split("SELECT ", 1)[1].rsplit("\nFROM DUAL", 1)[0])
        key, payload = _evaluate(key_sql, env), json.loads(_evaluate(value_sql, env))
        pair = (payload["model_id"], payload["family_id"])
        assert key == setting_key(pair) and payload["schema"] == LEGACY_SCHEMA     # 시드는 예전(1) 형식, 앱이 그대로 읽음
        current = _current({"setting_value": payload}, pair)                # 앱이 저장값을 읽을 때 쓰는 검사 그대로
        groups = current.entry.groups
        assert isinstance(groups, VersionGroups) and list(groups.to_dict()) == ["N"]
        assert [len(members) for members in groups.slots.values()] == [1]          # N 칸에 버전 하나
        assert current.entry.memo == "시연 기준: 최신 바로 아래 버전을 N"


def test_target_is_the_second_latest_applicable_release(built):
    build_seed, candidates, catalog, _ = built
    for model, key in sorted({(t["model"], t["family"]) for t in catalog["templates"] if t["model"] != "HPA"}):
        choices = candidates(catalog, model, key) if key in catalog["families"] else []
        if not choices:                                     # 근거가 없으면 목표를 만들지 않는다
            assert build_seed.demo_target(catalog, candidates, model, key) is None, (model, key)
            continue
        orders = sorted(version["order"] for _, version in choices)
        chosen = catalog["families"][key]["versions"][build_seed.demo_target(catalog, candidates, model, key)]["order"]
        assert chosen == (orders[-2] if len(orders) > 1 else orders[-1]), (model, key)


def test_existing_n_groups_block_the_seed_install(built):
    _, _, _, sql = built
    install = next(s for s in sql.split(";\n\n") if s.startswith("SET @seed_install"))
    assert "CONCAT('version_groups.v1:'" in install                    # 이미 저장한 N 그룹은 시드가 덮어쓰지 않는다
