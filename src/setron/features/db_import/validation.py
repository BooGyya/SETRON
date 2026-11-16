"""병합할 행을 메모리에 투영해 설비 연결과 날짜별 분류를 쓰기 전에 검사한다."""

from collections import defaultdict

from setron.features.storage.inputs import require
from .hierarchy import validate_hierarchy


def validate_merge(target, writes, repository):
    """변경된 설비와 모델에 연결된 전체 이력을 선형 탐색으로 검사한다.

    삭제될 PM·SW는 최종 상태에서 제외한다. 다른 설비의 기존 불완전한 이력은
    이번 병합의 차단 사유로 삼지 않으며, 적용 시에도 같은 검사를 다시 수행한다.
    """
    relevant = {"equipment_group", "version_family", "release_version", "equipment_snapshot",
                "equipment_version", "equipment_pm", "risk_rule"}
    changed = [write for write in writes if write.table in relevant]
    primary = {name: tuple(repository.table(name).primary_key.columns.keys()) for name in target}

    def key(name, row):
        return tuple(row[column] for column in primary[name])

    final = {name: {key(name, row): row for row in rows} for name, rows in target.items()}
    for write in writes:
        if write.action == "delete":
            final[write.table].pop(key(write.table, write.key), None)
        else:
            final[write.table][key(write.table, write.row)] = write.row
    final = {name: tuple(rows.values()) for name, rows in final.items()}
    if changed or any(write.table == "app_setting" for write in writes):
        validate_hierarchy(final)
    if not changed:
        return

    group_ids = {write.key["group_id"] for write in changed if write.table == "equipment_group"}
    family_ids = {write.key["family_id"] for write in changed if write.table == "version_family"}
    release_ids = {write.key["release_version_id"] for write in changed if write.table == "release_version"}
    snapshot_ids = {write.row["snapshot_id"] for write in changed
                    if write.table in {"equipment_snapshot", "equipment_version", "equipment_pm"}}
    affected = set()
    for rows in (target, final):
        families = family_ids | {row["family_id"] for row in rows["version_family"]
                                 if row["group_id"] in group_ids}
        snapshots = snapshot_ids | {row["snapshot_id"] for row in rows["equipment_pm"]
                                    if row["group_id"] in group_ids}
        snapshots |= {row["snapshot_id"] for row in rows["equipment_version"]
                      if row["family_id"] in families or row["release_version_id"] in release_ids}
        affected.update(row["equipment_id"] for row in rows["equipment_snapshot"]
                        if row["snapshot_id"] in snapshots or row["group_id"] in group_ids)
    _validate_equipment(final, affected)
    changed_risks = {write.key["risk_rule_id"] for write in changed if write.table == "risk_rule"}
    releases = {row["release_version_id"]: row for row in final["release_version"]}
    for row in final["risk_rule"]:
        references = [row[field] for field in ("release_version_id", "start_release_version_id", "end_release_version_id")
                      if row[field] is not None]
        if row["risk_rule_id"] in changed_risks or any(value in release_ids for value in references):
            require(all(value in releases and releases[value]["family_id"] == row["family_id"] for value in references),
                    "위험 정보와 연결 릴리즈의 버전 계열이 다릅니다.")


def _validate_equipment(rows, affected):
    groups = {row["group_id"]: row for row in rows["equipment_group"]}
    families = {row["family_id"]: row for row in rows["version_family"]}
    releases = {row["release_version_id"]: row for row in rows["release_version"]}
    equipment = {row["equipment_id"]: row for row in rows["equipment"]}
    snapshots = {row["snapshot_id"]: row for row in rows["equipment_snapshot"]
                 if row["equipment_id"] in affected}
    versions, by_snapshot, pm_groups = {}, defaultdict(list), defaultdict(set)
    software_models = defaultdict(set)
    for row in rows["equipment_version"]:
        if row["snapshot_id"] in snapshots:
            versions[row["equipment_version_id"]] = row
            by_snapshot[row["snapshot_id"]].append(row)
    for row in rows["equipment_pm"]:
        if row["snapshot_id"] not in snapshots:
            continue
        pm_groups[row["snapshot_id"]].add(row["group_id"])
        if row["equipment_version_id"] is not None:
            version = versions.get(row["equipment_version_id"])
            require(version is not None and version["snapshot_id"] == row["snapshot_id"],
                    "PM의 적용 버전이 같은 설비 현황에 속하지 않습니다.")
            software_models[row["equipment_version_id"]].add(row["group_id"])

    history = {}
    for snapshot_id, snapshot in snapshots.items():
        models = pm_groups[snapshot_id] | ({snapshot["group_id"]} if snapshot["group_id"] is not None else set())
        require(all(model in groups for model in models),
                "설비 현황의 모델 분류를 확인할 수 없습니다. 모델과 PM 구성을 확인해 주세요.")
        excluded = {model for model in models if groups[model]["category"] == "HPA"
                    or not groups[model]["analysis_enabled"]}
        require(not excluded or excluded == models,
                "분석 제외 모델과 분석 모델이 한 설비에 섞여 있습니다. HPA와 일반 설비를 구분해야 합니다.")
        if models:
            kind = "HPA" if excluded else "일반 설비"
            equipment_id = snapshot["equipment_id"]
            previous = history.setdefault(equipment_id, (kind, snapshot["base_date"]))
            code = equipment[equipment_id]["equipment_code"]
            code = code.decode("utf-8") if isinstance(code, bytes) else code
            require(previous[0] == kind,
                    f"{code}: {previous[1]} 관측은 {previous[0]}, {snapshot['base_date']} 관측은 {kind}로 "
                    "날짜별 분류가 다릅니다. 호기와 분류의 원본을 확인해 주세요.")
        for version in by_snapshot[snapshot_id]:
            family = families.get(version["family_id"])
            require(family is not None and family["group_id"] in groups,
                    "적용 SW의 버전 계열과 설비군을 확인할 수 없습니다.")
            owner = groups[family["group_id"]]
            version_models = software_models[version["equipment_version_id"]] or models
            require(all(groups[model]["category"] == owner["category"] for model in version_models),
                    "PM 모델과 버전 계열의 설비군이 다릅니다. KCTC O2와 Oxide O2를 구분해야 합니다.")
            release_id = version["release_version_id"]
            if release_id is not None:
                release = releases.get(release_id)
                require(release is not None and release["family_id"] == version["family_id"],
                        "적용 SW와 연결 릴리즈의 버전 계열이 다릅니다.")
