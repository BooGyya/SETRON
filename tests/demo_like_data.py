"""시연 DB와 같은 자료를 DB 없이 만든다 (11/11 QA용).

database/seed_source.make_plan(시연 시드 생성 계획)과 demo_release_catalog.json을 그대로 써서
설비 300대 × 기준일 6개(2026-05-21 ~ 10-21), 실제 사업장 · 라인(V1 · V2 · P1F …) · 모델 · 계열(C3 구UI · 통합UI …) ·
릴리즈 순서 · 시연 목표 N(최신 바로 아래)을 가진 ChatData를 돌려준다.

시연 DB에만 있는 이름 때문에 생기던 문제('V1.2.1.4'의 V1을 라인으로, V2 라인을 버전으로)를 테스트에서 잡기 위한 자료다.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.chat import ChatData
from setron.features.query.schemas import FamilyChoice, GroupChoice, QueryCatalog
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "database"
if str(DATABASE) not in sys.path:
    sys.path.insert(0, str(DATABASE))

from build_seed import MODELS, demo_target  # noqa: E402  (database/ 모듈)
from seed_source import DATES, FAMILIES, candidates, make_plan  # noqa: E402


def _group_name(model: str) -> str:
    return model[2:] if model.startswith("M-") else model


def build() -> tuple[QueryCatalog, ChatData]:
    catalog_json = json.loads((DATABASE / "demo_release_catalog.json").read_text(encoding="utf-8"))
    group_ids = {model: index for index, (model, _, _) in enumerate(MODELS, 1)}
    groups = tuple(GroupChoice(group_ids[model], _group_name(model), model, category) for model, category, _ in MODELS
                   if model != "HPA")
    family_ids = {key: index for index, (key, _, _, _) in enumerate(FAMILIES, 1)}
    families = tuple(FamilyChoice(family_ids[key], name, group_ids[owner]) for key, owner, name, _ in FAMILIES
                     if key != "hpa")
    family_names = {family_ids[key]: name for key, _, name, _ in FAMILIES}
    releases: dict[int, FamilyReleases] = {}
    release_ids: dict[tuple[str, int], int] = {}
    next_id = 1000
    for key, _, _, _ in FAMILIES:
        rows = []
        for index, version in enumerate(catalog_json["families"].get(key, {}).get("versions", [])):
            next_id += 1
            release_ids[(key, index)] = next_id
            rows.append(ReleaseVersion(next_id, family_ids[key], version["name"], version["order"],
                                       date.fromisoformat(version["date"])))
        releases[family_ids[key]] = FamilyReleases(family_ids[key], rows)
    templates = catalog_json["templates"]
    records = []
    for number, item in enumerate(make_plan(catalog_json), 1):
        template = templates[item["template"]]
        model, key = template["model"], template["family"]
        hpa = model == "HPA"
        for observation in item["observations"]:
            day = date.fromisoformat(observation["date"])
            pms = () if hpa else (PmVersion(None, group_ids[model], family_ids[key], observation["version"],
                                            release_ids.get((key, observation["release"]))),)
            records.append(EquipmentRecord(number, item["code"], day, template["site"], template["line"], model, pms,
                                           is_hpa=hpa))
    targets = {}
    pairs = sorted({(t["model"], t["family"]) for t in templates if t["model"] != "HPA"})
    for model, key in pairs:
        chosen = demo_target(catalog_json, candidates, model, key)
        if chosen is not None:
            targets[(group_ids[model], family_ids[key])] = release_ids[(key, chosen)]
    sites = tuple(sorted({t["site"] for t in templates}))
    lines = tuple(sorted({t["line"] for t in templates if t["line"]}))
    catalog = QueryCatalog(sites=sites, lines=lines, groups=groups, families=families, dates=tuple(DATES),
                           releases=releases)
    return catalog, ChatData(tuple(records), catalog, targets, family_names)


CATALOG, DATA = build()
