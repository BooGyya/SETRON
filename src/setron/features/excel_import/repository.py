"""BE-008 검증에 필요한 관리 자료를 같은 트랜잭션에서 읽는다."""

from setron.features.reference_data import strip_foundry
from datetime import date

from setron.features.equipment.repository import EquipmentRepository
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

from .reference import FamilyReference, ModelReference, ReferenceData, StoredRelease, family_key, resolve_family
from .schemas import ExtractKind, ReviewRow
from .service import _safe


class ImportValidationRepository:
    """가져오기 검증용 읽기 전용 레포지토리."""

    def load(self, tx, drafts, *, targets=None, aliases=None) -> ReferenceData:
        """확인된 모델·계열·릴리즈와 요청 호기·날짜의 기존값을 읽는다.

        param
        - tx: 묶음 전체에서 공유하는 공통 저장 트랜잭션.
        - drafts: 함께 검증할 전체 파일·시트 초안.
        - targets: (모델 ID, 계열 ID)별 목표 릴리즈 ID. None이면 목표 미지정.
        - aliases: 계열 ID별 확정 버전 별칭. None이면 별칭을 추정하지 않음.

        return
        - 같은 트랜잭션에서 읽은 검증 기준 자료. DB 값을 변경하지 않음.

        raises
        - StorageValidationError: 기존 JSON·PM 연결을 읽을 수 없는 경우.
        - ValueError: 등록 릴리즈 원문 또는 확정 별칭 대응이 상충하는 경우.
        """
        repository = EquipmentRepository()
        groups = {row["group_id"]: row for row in repository.rows(tx, "equipment_group")}
        models = tuple(ModelReference(row["group_id"], row["group_name"], row["category"], row.get("parent_group_id"),
                                      analysis_enabled=bool(row["analysis_enabled"]))
                       for row in groups.values())
        family_rows = repository.rows(tx, "version_family")
        references = []
        for row in family_rows:
            group = groups[row["group_id"]]
            # 'O2 (파운드리)' 같은 파운드리용 계열은 같은 구분 키에 영역 표시를 따로 둔다 (reference_data.area_of 로 선택).
            plain, foundry = strip_foundry(row["family_name"])
            key = family_key(ReviewRow(1, {
                "group_name": group["category"], "model_name": group["group_name"],
                "family_name": plain, "ui_type": row["ui_type"],
            }), ExtractKind.EQUIPMENT_STATUS)
            if key is not None:
                references.append(FamilyReference(key, FamilyReleases(row["family_id"], ()), owner_group_id=row["group_id"],
                                                  production_area="FOUNDRY" if foundry else None))
        catalog = ReferenceData(frozenset(), families=tuple(references), catalog_loaded=True, models=models)
        needed = set()
        for draft in drafts:
            for row in draft.active_rows:
                family = resolve_family(row, draft.kind, catalog, sheet_name=draft.sheet.sheet_name,
                                        workbook_name=draft.workbook_name)
                if family is not None:
                    needed.add(family.releases.family_id)
        stored = []
        families = []
        for family in references:
            family_id = family.releases.family_id
            releases = repository.rows(tx, "release_version", family_id=family_id) if family_id in needed else ()
            versions = tuple(ReleaseVersion(r["release_version_id"], family_id, r["version_raw"],
                                            r["version_order"], r["released_at"]) for r in releases)
            target_values = {group["group_name"]: target for (group_id, target_family), target in (targets or {}).items()
                             if target_family == family_id and (group := groups.get(group_id)) is not None
                             and group["category"] == family.key.category}
            families.append(FamilyReference(family.key,
                FamilyReleases(family_id, versions, (aliases or {}).get(family_id) if family_id in needed else None,
                               exact_raw=True),
                target_values, family.owner_group_id, family.production_area))
            if not any(d.kind is ExtractKind.RELEASE_NOTE for d in drafts):
                continue
            for release in releases:
                common = {"release_date": release["released_at"].isoformat() if release["released_at"] else None}
                links = repository.rows(tx, "release_version_item", release_version_id=release["release_version_id"])
                if not links:
                    stored.append(StoredRelease(family_id, release["version_raw"], {**common, "item_no": None}))
                for link in links:
                    item = tx.get("item", {"item_id": link["item_id"]})
                    stored.append(StoredRelease(family_id, release["version_raw"], {
                        **common, "item_no": str(item["item_no"]) if item["item_no"] is not None else None,
                        "content": item["content"],
                    }))
        equipment = {}
        equipment_sites = {}
        equipment_classifications = {}
        snapshots = {}
        for draft in drafts:
            if draft.kind is not ExtractKind.EQUIPMENT_STATUS:
                continue
            for row in draft.active_rows:
                code = row.value("equipment_code")
                if not isinstance(code, str) or not code.strip():
                    continue
                if code not in equipment:
                    equipment[code] = repository.find_equipment(tx, code)
                    if equipment[code] is not None:
                        history = repository.rows(tx, "equipment_snapshot", equipment_id=equipment[code]["equipment_id"])
                        equipment_sites[code] = frozenset(snapshot["site"] for snapshot in history)
                        equipment_classifications[code] = {}
                        for snapshot in history:
                            model_ids = {pm["group_id"] for pm in repository.rows(
                                tx, "equipment_pm", snapshot_id=snapshot["snapshot_id"])}
                            if snapshot["group_id"] is not None:
                                model_ids.add(snapshot["group_id"])
                            equipment_classifications[code][snapshot["base_date"].isoformat()] = frozenset(
                                groups[model_id]["category"] == "HPA" or not groups[model_id]["analysis_enabled"]
                                for model_id in model_ids)
                if equipment[code] is None:
                    continue
                normalized_date = _safe(row, "base_date")
                if normalized_date is None:
                    continue
                key = code, normalized_date
                if key in snapshots:
                    continue
                rows = repository.rows(tx, "equipment_snapshot", equipment_id=equipment[code]["equipment_id"],
                                       base_date=date.fromisoformat(normalized_date))
                snapshots[key] = repository.load(tx, equipment[code], rows[0]) if rows else None
        return ReferenceData(frozenset(code for code, value in equipment.items() if value is not None),
                             families=tuple(families), snapshots=tuple(value for value in snapshots.values() if value is not None),
                             stored_releases=tuple(stored), catalog_loaded=True, models=models,
                             equipment_sites=equipment_sites, equipment_classifications=equipment_classifications)
