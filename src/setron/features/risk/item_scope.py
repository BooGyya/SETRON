"""Item 관리의 표시 정보와 명시적 모델/위치 범위. 기존 재연결 JSON에 이름공간을 분리해 보관한다."""

CATEGORIES = ("환경안전 필수", "결함 버전", "공정 위험", "기타")


def scope_fields(reference):
    value = (reference or {}).get("item_manager")
    if value is None:
        return {}
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value.get("schema") != 1:
        raise ValueError("Item 관리 범위 형식이 올바르지 않습니다.")
    if set(value) != {"schema", "name", "category", "memo", "model_ids", "site", "line"}:
        raise ValueError("Item 관리 범위 항목이 올바르지 않습니다.")
    for name, limit in (("name", 250), ("memo", 4000)):
        if not isinstance(value[name], str) or len(value[name]) > limit:
            raise ValueError("Item 이름 또는 메모 길이를 확인해 주세요.")
    if not value["name"].strip() or value["category"] not in CATEGORIES:
        raise ValueError("Item 이름과 구분이 필요합니다.")
    models = value["model_ids"]
    if not isinstance(models, list) or not models or len(models) > 100 or any(type(i) is not int or i <= 0 for i in models) or len(set(models)) != len(models):
        raise ValueError("대상 설비군을 하나 이상 선택해 주세요.")
    if any(v is not None and (not isinstance(v, str) or not v.strip() or len(v) > 100)
           for v in (value["site"], value["line"])) or value["line"] is not None and value["site"] is None:
        raise ValueError("Line의 Site 범위를 확인해 주세요.")
    return dict(display_name=value["name"].strip(), category=value["category"], memo=value["memo"],
                target_model_ids=tuple(models), target_site=value["site"], target_line=value["line"])


def validate_scope_in(tx, reference, family_id):
    fields = scope_fields(reference)
    if not fields:
        return
    family = tx.get("version_family", {"family_id": family_id})
    owner = tx.get("equipment_group", {"group_id": family["group_id"]})
    for mid in fields["target_model_ids"]:
        group = tx.get("equipment_group", {"group_id": mid})
        if group["category"] != owner["category"] or not group["analysis_enabled"]:
            raise ValueError("대상 설비군은 같은 분류의 분석 대상 모델이어야 합니다.")
    site, line = fields["target_site"], fields["target_line"]
    if site is not None:
        from setron.features.equipment.repository import EquipmentRepository
        snapshots = EquipmentRepository().rows(tx, "equipment_snapshot", site=site)
        if not snapshots or line is not None and not any(r["line"] == line for r in snapshots):
            raise ValueError("대상 Site·Line이 저장된 관측 자료에 없습니다.")
