"""날짜별 설비 현황 신규 저장과 검토를 마친 부분 재가져오기를 처리한다."""

from dataclasses import fields
from copy import deepcopy
from datetime import date

from .repository import EquipmentRepository
from .schemas import EquipmentSourceCell, SnapshotInput, StoredSnapshot
from .validation import prepare_snapshot, require, required_text


class EquipmentService:
    """설비 현황 저장·조회 서비스.

    DB 작업은 공통 저장 서비스의 트랜잭션을 사용한다. 최종 저장 중 연결이 끊겨
    결과를 확인할 수 없으면 DatabaseCommitUnknownError를 전달하며 재시도하지 않는다.
    """

    def __init__(self, storage):
        """공통 저장 서비스를 연결하고 설비 레포지토리를 초기화한다.

        param
        - storage: DB 접속과 업무 단위 트랜잭션을 관리하는 StorageService.
        """
        self.storage = storage
        self.repository = EquipmentRepository()

    def resolve_model(self, model_name: str, category: str) -> dict:
        """모델명과 설비군 분류에 일치하는 등록 모델을 조회한다.

        확정 별칭인 M5는 M-O5로 조회하고, M-O5 Prime은 별도 모델로 구분한다.

        param
        - model_name: 조회할 모델 이름 또는 확정 별칭.
        - category: 최상위 설비군 분류. POLY, OXIDE, KCTC, HPA 중 하나.

        return
        - 이름과 분류가 일치하는 equipment_group 행.

        raises
        - StorageValidationError: 입력이 잘못됐거나 일치하는 모델이 없거나 여러 개인 경우.
        """
        required_text(model_name, "model_name", 255)
        require(isinstance(category, str) and category in {"POLY", "OXIDE", "KCTC", "HPA"}, "설비군 분류를 확인해야 합니다.")
        canonical = "M-O5" if model_name == "M5" else model_name
        with self.storage.transaction() as tx:
            matches = [row for row in self.repository.rows(tx, "equipment_group", category=category)
                       if row["group_name"] == canonical]
            require(len(matches) == 1, "모델 대응이 없거나 여러 개입니다. 원본과 등록 모델을 확인해야 합니다.")
            return matches[0]

    def save_snapshots(self, snapshots: tuple[SnapshotInput, ...]) -> tuple[StoredSnapshot, ...]:
        """설비 현황 묶음을 하나의 트랜잭션으로 저장한다.

        입력 검사나 최종 저장 전 작업이 실패하면 묶음 전체를 취소한다.
        최종 저장 중 연결이 끊긴 경우에는 반영 여부를 확인할 수 없음을 알린다.
        이미 저장된 같은 호기·기준일의 현황은 덮어쓰지 않는다.

        param
        - snapshots: 검토를 마친 SnapshotInput의 비어 있지 않은 튜플.

        return
        - 입력 순서대로 구성한 StoredSnapshot 튜플.

        raises
        - StorageValidationError: 입력이 잘못됐거나 같은 호기·기준일의 현황이 중복된 경우.
        - LookupError: 연결할 입력 작업·모델·계열·릴리즈가 DB에 없는 경우.
        - DatabaseConnectionError: 저장 작업을 시작하기 전에 DB 접속에 실패한 경우.
        - DatabaseSaveError: DB 저장 중 오류가 발생한 경우.
        - DatabaseCommitUnknownError: 최종 저장 중 연결이 끊겨 반영 여부를 확인할 수 없는 경우.
        """
        with self.storage.transaction() as tx:
            return self.save_snapshots_in(tx, snapshots)

    def save_snapshots_in(self, tx, snapshots: tuple[SnapshotInput, ...]) -> tuple[StoredSnapshot, ...]:
        """호출자가 연 트랜잭션 안에서 설비 현황 묶음을 저장한다.

        입력 작업 생성 등 다른 저장과 한 번에 확정해야 할 때 쓴다. 확정·취소는 호출자가 관리한다.

        param
        - tx: 호출자가 연 StorageTransaction.
        - snapshots: 검토를 마친 SnapshotInput의 비어 있지 않은 튜플.

        return
        - 입력 순서대로 구성한 StoredSnapshot 튜플.

        raises
        - StorageValidationError: 입력이 잘못됐거나 같은 호기·기준일의 현황이 중복된 경우.
        - LookupError: 연결할 입력 작업·모델·계열·릴리즈가 DB에 없는 경우.
        """
        require(isinstance(snapshots, tuple) and bool(snapshots), "저장할 현황 묶음이 필요합니다.")
        prepared = tuple(prepare_snapshot(snapshot) for snapshot in snapshots)
        keys = [(value.equipment_code, value.base_date) for value in prepared]
        require(len(set(keys)) == len(keys), "같은 호기·기준일의 현황이 중복됐습니다. 저장 전에 합쳐야 합니다.")
        return tuple(self._save(tx, value) for value in prepared)

    def _save(self, tx, value):
        """공통 트랜잭션 안에서 설비 현황 한 건과 적용 SW·PM을 저장한다.

        원본 위치와 연결 정보를 확인한 뒤 호기, 기준일별 현황, SW, PM 순서로 저장한다.
        저장 확정은 save_snapshots에서 관리한다.

        param
        - tx: 현황 묶음 전체가 공유하는 저장 트랜잭션.
        - value: 기본 입력 검사와 중복 정리를 마친 SnapshotInput.

        return
        - 저장된 SW·PM을 다시 조회해 구성한 StoredSnapshot.

        raises
        - StorageValidationError: 출처·계열 연결이 잘못됐거나 기존 현황이 있는 경우.
        - LookupError: 연결할 입력 작업·모델·계열·릴리즈가 DB에 없는 경우.
        """
        self._validate_links(tx, value)
        equipment = self.repository.find_equipment(tx, value.equipment_code, lock=True)
        if equipment is None:
            equipment = tx.insert("equipment", {"equipment_code": value.equipment_code})
        self._check_site(tx, equipment, value.site)
        existing = self.repository.rows(tx, "equipment_snapshot", equipment_id=equipment["equipment_id"], base_date=value.base_date)
        require(not existing, "같은 호기·기준일의 현황이 이미 있습니다. 재가져오기 검토가 필요합니다.")
        group_ids = {pm.group_id for software in value.software for pm in software.pms}
        if value.group_id is not None:
            group_ids.add(value.group_id)
        self._check_hpa_history(tx, equipment, value.base_date, group_ids)
        snapshot = tx.insert("equipment_snapshot", {
            **{field.name: getattr(value, field.name) for field in fields(SnapshotInput)
               if field.name not in {"equipment_code", "software"}},
            "equipment_id": equipment["equipment_id"],
        })
        for software in value.software:
            version = self._insert_version(tx, snapshot["snapshot_id"], value.batch_id, software)
            for pm in software.pms:
                tx.insert("equipment_pm", {
                    "snapshot_id": snapshot["snapshot_id"], "equipment_version_id": version["equipment_version_id"],
                    "pm_name": pm.pm_name, "group_id": pm.group_id, "model_name_raw": pm.model_name_raw,
                    "batch_id": value.batch_id, "source_reference": pm.source_reference,
                })
        return self.repository.load(tx, equipment, snapshot)

    @staticmethod
    def _insert_version(tx, snapshot_id, batch_id, software):
        return tx.insert("equipment_version", {
            "snapshot_id": snapshot_id, "family_id": software.family_id,
            "version_raw": software.version_raw, "release_version_id": software.release_version_id,
            "batch_id": batch_id, "source_reference": software.source_reference,
        })

    def _validate_links(self, tx, value):
        """신규 저장과 재가져오기에서 같은 출처·모델·계열·릴리즈 검사를 사용한다."""
        batch = tx.get("import_batch", {"batch_id": value.batch_id})
        self._check_sources(tx, batch, value.source_reference)
        group_ids = {pm.group_id for software in value.software for pm in software.pms}
        if value.group_id is not None:
            group_ids.add(value.group_id)
        groups = self._check_model_mix(tx, group_ids)
        for software in value.software:
            self._check_sources(tx, batch, software.source_reference)
            family = tx.get("version_family", {"family_id": software.family_id})
            owner = tx.get("equipment_group", {"group_id": family["group_id"]})
            models = {pm.group_id for pm in software.pms} or {value.group_id}
            require(all(groups[model]["category"] == owner["category"] for model in models),
                    "PM 모델과 버전 계열의 설비군이 다릅니다. KCTC O2와 Oxide O2를 구분해야 합니다.")
            if software.release_version_id is not None:
                release = tx.get("release_version", {"release_version_id": software.release_version_id})
                require(release["family_id"] == software.family_id, "적용 SW와 연결 릴리즈의 버전 계열이 다릅니다.")
            for pm in software.pms:
                self._check_sources(tx, batch, pm.source_reference)

    @staticmethod
    def _check_model_mix(tx, group_ids):
        """최종 모델 구성에 HPA 등 분석 제외 모델과 일반 모델이 섞이지 않게 한다."""
        groups = {group_id: tx.get("equipment_group", {"group_id": group_id}) for group_id in sorted(group_ids)}
        excluded = {group_id for group_id, group in groups.items()
                    if group["category"] == "HPA" or group.get("analysis_enabled") is not None
                    and not group["analysis_enabled"]}
        require(not excluded or excluded == set(group_ids),
                "분석 제외 모델과 분석 모델이 한 설비에 섞여 있습니다. HPA와 일반 설비를 구분해야 합니다.")
        return groups

    def _check_hpa_history(self, tx, equipment, base_date, group_ids):
        """다른 날짜의 HPA·일반 구분과 충돌하면 원본 확인 전까지 전체 저장을 보류한다."""
        current = self._check_model_mix(tx, group_ids)

        def kind(groups):
            return "HPA" if any(group["category"] == "HPA" or group.get("analysis_enabled") is not None
                                and not group["analysis_enabled"] for group in groups.values()) else "일반 설비"

        current_kind = kind(current)
        for snapshot in self.repository.rows(tx, "equipment_snapshot", lock=True, equipment_id=equipment["equipment_id"]):
            if snapshot["base_date"] == base_date:
                continue
            stored_ids = {pm["group_id"] for pm in self.repository.rows(
                tx, "equipment_pm", lock=True, snapshot_id=snapshot["snapshot_id"])}
            if snapshot["group_id"] is not None:
                stored_ids.add(snapshot["group_id"])
            require(bool(stored_ids),
                    f"{equipment['equipment_code']}: {snapshot['base_date']} 관측의 모델 분류를 확인할 수 없습니다. "
                    "원본을 확인하기 전까지 이번 저장 묶음 전체를 보류합니다.")
            stored_kind = kind(self._check_model_mix(tx, stored_ids))
            require(stored_kind == current_kind,
                    f"{equipment['equipment_code']}: {snapshot['base_date']} 관측은 {stored_kind}, "
                    f"{base_date} 저장 대상은 {current_kind}로 날짜별 분류가 다릅니다. "
                    "호기와 분류의 원본을 확인하기 전까지 이번 저장 묶음 전체를 보류합니다.")

    def _check_site(self, tx, equipment, site):
        """사업장은 호기의 고정 소속이다. 다른 날짜의 Excel도 소속을 바꾸지 못한다."""
        sites = {row["site"] for row in self.repository.rows(
            tx, "equipment_snapshot", lock=True, equipment_id=equipment["equipment_id"],
        )}
        require(not sites or sites == {site},
                f"{equipment['equipment_code']}: 등록된 사업장과 다릅니다. 사업장은 변경할 수 없습니다. "
                "설비 호기와 Excel 사업장을 확인해 주세요.")

    def import_snapshots_in(self, tx, snapshots: tuple[SnapshotInput, ...], *, metadata_keys=None) -> tuple[StoredSnapshot, ...]:
        """검토 기준을 확인한 호출자의 트랜잭션에서 신규 저장·부분 갱신한다.

        DatabaseImportSaver가 전체 검사와 기존값 검토 여부를 확인한 뒤 호출한다.
        입력에 없는 날짜·계열·고정 PM 위치는 유지하며 사업장은 변경하지 않는다.
        """
        return self.import_snapshot_batches_in(tx, (snapshots,), metadata_keys_by_batch=(metadata_keys,))[0]

    def import_snapshot_batches_in(self, tx, batches: tuple[tuple[SnapshotInput, ...], ...], *,
                                   metadata_keys_by_batch=None) -> tuple[tuple[StoredSnapshot, ...], ...]:
        """원본 입력 작업별 묶음을 적용하고 모든 시트가 반영된 최종 구성을 검사한다.

        같은 현황의 PM이 여러 시트에 나뉘어도 중간 혼합 상태로 오차단하지 않는다.
        원래의 다른 날짜 이력은 쓰기 전에 확인하고 최종 PM·날짜별 분류를 다시 검사한다.
        출처와 batch_id는 합치지 않으며, 오류는 호출자가 연 트랜잭션 전체를 취소해야 한다.

        param
        - tx, batches: 저장 트랜잭션과 원본 입력 작업별 SnapshotInput 튜플.
        - metadata_keys_by_batch: 각 묶음이 갱신할 (호기, 날짜) 집합. None은 해당 묶음 전체.

        return
        - 각 입력 묶음의 순서에 대응하는 최종 저장 현황. 중간 상태는 반환하지 않음.
        """
        require(isinstance(batches, tuple) and bool(batches), "저장할 현황 묶음이 필요합니다.")
        prepared_batches = []
        for snapshots in batches:
            require(isinstance(snapshots, tuple) and bool(snapshots), "저장할 현황 묶음이 필요합니다.")
            prepared = tuple(prepare_snapshot(value) for value in snapshots)
            keys = [(value.equipment_code, value.base_date) for value in prepared]
            require(len(set(keys)) == len(keys), "같은 호기·기준일의 현황이 중복됐습니다. 저장 전에 합쳐야 합니다.")
            prepared_batches.append(prepared)
        if metadata_keys_by_batch is None:
            metadata_keys_by_batch = (None,) * len(prepared_batches)
        require(isinstance(metadata_keys_by_batch, tuple) and len(metadata_keys_by_batch) == len(prepared_batches),
                "각 원본 묶음의 공통정보 갱신 범위를 확인해야 합니다.")
        values = tuple(value for prepared in prepared_batches for value in prepared)
        equipment_by_code = {code: self.repository.find_equipment(tx, code, lock=True)
                             for code in sorted({value.equipment_code for value in values})}
        # 같은 요청에 포함된 다른 날짜를 먼저 덮어써 원래의 분류 충돌을 숨기지 않는다.
        for value in values:
            self._validate_links(tx, value)
            equipment = equipment_by_code[value.equipment_code]
            if equipment is not None:
                self._check_site(tx, equipment, value.site)
                groups = {pm.group_id for software in value.software for pm in software.pms}
                if value.group_id is not None:
                    groups.add(value.group_id)
                self._check_hpa_history(tx, equipment, value.base_date, groups)
        for prepared, metadata_keys in zip(prepared_batches, metadata_keys_by_batch):
            for value in prepared:
                equipment = self.repository.find_equipment(tx, value.equipment_code, lock=True)
                existing = self.repository.rows(tx, "equipment_snapshot", lock=True,
                    equipment_id=equipment["equipment_id"], base_date=value.base_date) if equipment else []
                if not existing:
                    self._save(tx, value)
                    continue
                self._check_site(tx, equipment, value.site)
                update_metadata = metadata_keys is None or (value.equipment_code, value.base_date) in metadata_keys
                self._merge(tx, equipment, existing[0], value, update_metadata=update_metadata)
        final = {}
        for code, day in sorted({(value.equipment_code, value.base_date) for value in values}):
            equipment = self.repository.find_equipment(tx, code, lock=True)
            snapshot = self.repository.rows(tx, "equipment_snapshot", lock=True,
                equipment_id=equipment["equipment_id"], base_date=day)[0]
            groups = {pm["group_id"] for pm in self.repository.rows(tx, "equipment_pm", lock=True,
                                                                   snapshot_id=snapshot["snapshot_id"])}
            if snapshot["group_id"] is not None:
                groups.add(snapshot["group_id"])
            self._check_hpa_history(tx, equipment, day, groups)
            final[code, day] = self.repository.load(tx, equipment, snapshot, lock=True)
        return tuple(tuple(final[value.equipment_code, value.base_date] for value in prepared)
                     for prepared in prepared_batches)

    def _merge(self, tx, equipment, snapshot, value, *, update_metadata=True):
        """공유 SW를 분리·재연결하며 가져온 고정 PM 위치만 갱신한다."""
        snapshot_id = snapshot["snapshot_id"]
        old = self.repository.load(tx, equipment, snapshot, lock=True)
        has_pm = any(sw.pms for sw in old.software)
        require(has_pm == any(sw.pms for sw in value.software),
                "기존 현황과 PM 구분이 다릅니다. PM 연결을 임의로 바꾸지 않고 전체 저장을 보류합니다.")
        if not has_pm and old.group_id != value.group_id:
            untouched_families = {sw.family_id for sw in old.software} - {sw.family_id for sw in value.software}
            require(not untouched_families,
                    "설비 모델이 변경되지만 기존의 다른 SW 계열이 이번 검토에 포함되지 않았습니다. "
                    "다른 계열의 모델을 바꾸지 않도록 기존 모든 계열을 함께 검토해 주세요.")
        versions = {(sw.family_id, sw.version_raw): sw for sw in old.software}
        old_pms = {pm.pm_name: pm for sw in old.software for pm in sw.pms}
        incoming_names = {pm.pm_name for sw in value.software for pm in sw.pms}
        for sw in value.software:
            key = sw.family_id, sw.version_raw
            previous = versions.get(key)
            if previous is not None:
                version_id = previous.equipment_version_id
                # 가져오지 않은 PM이 공유하는 SW의 대표 출처는 그대로 둔다.
                # 각 PM에는 자신의 최신 SW 출처와 입력 작업을 별도로 보관한다.
                if not previous.pms or all(pm.pm_name in incoming_names for pm in previous.pms):
                    tx.update("equipment_version", {"equipment_version_id": version_id}, {
                        "release_version_id": (sw.release_version_id if sw.release_version_id is not None
                                               else previous.release_version_id),
                        "batch_id": value.batch_id, "source_reference": sw.source_reference,
                    })
                else:
                    require(previous.release_version_id in {None, sw.release_version_id}
                            or sw.release_version_id is None,
                            "같은 버전 원문의 기존 릴리즈 연결과 검토 연결이 다릅니다. 연결을 다시 확인해 주세요.")
                    if previous.release_version_id is None and sw.release_version_id is not None:
                        tx.update("equipment_version", {"equipment_version_id": version_id}, {
                            "release_version_id": sw.release_version_id,
                        })
            elif not has_pm:
                candidates = [stored for stored in old.software if stored.family_id == sw.family_id]
                require(len(candidates) <= 1, "PM 구분 없는 기존 계열에 여러 SW 버전이 있습니다.")
                if candidates:
                    version_id = candidates[0].equipment_version_id
                    tx.update("equipment_version", {"equipment_version_id": version_id}, {
                        "version_raw": sw.version_raw, "release_version_id": sw.release_version_id,
                        "batch_id": value.batch_id, "source_reference": sw.source_reference,
                    })
                else:
                    version_id = self._insert_version(tx, snapshot_id, value.batch_id, sw)["equipment_version_id"]
            else:
                version_id = self._insert_version(tx, snapshot_id, value.batch_id, sw)["equipment_version_id"]
            for pm in sw.pms:
                changes = {
                    "equipment_version_id": version_id, "group_id": pm.group_id,
                    "model_name_raw": pm.model_name_raw, "batch_id": value.batch_id,
                    "source_reference": pm.source_reference,
                }
                if pm.pm_name in old_pms:
                    tx.update("equipment_pm", {"pm_id": old_pms[pm.pm_name].pm_id}, changes)
                else:
                    tx.insert("equipment_pm", {"snapshot_id": snapshot_id, "pm_name": pm.pm_name, **changes})
        pms = self.repository.rows(tx, "equipment_pm", snapshot_id=snapshot_id)
        if has_pm:
            referenced = {pm["equipment_version_id"] for pm in pms}
            for sw in self.repository.rows(tx, "equipment_version", snapshot_id=snapshot_id):
                if sw["equipment_version_id"] not in referenced:
                    tx.repository.delete(tx.session, "equipment_version", {"equipment_version_id": sw["equipment_version_id"]})
        groups = {pm["group_id"] for pm in pms}
        # 입력에 없는 PM도 유지한다. 모든 원본 묶음의 적용 후 호출자가 최종 분류를 검사한다.
        representative = next(iter(groups)) if len(groups) == 1 else None
        changes = {field.name: getattr(value, field.name) for field in fields(SnapshotInput)
                   if field.name not in {"equipment_code", "base_date", "site", "software", "group_id"}} if update_metadata else {}
        if has_pm and update_metadata and value.model_name != old.model_name:
            # PM 행의 모델은 전체 설비의 모델 원문을 대체하지 않는다.
            # 다른 공통정보가 새 입력 작업을 사용하더라도 모델 원본의 작업은 별도로 유지한다.
            changes["model_name"] = old.model_name
            sources = deepcopy(value.source_reference) or {}
            model_source = deepcopy((old.source_reference or {}).get("model_name"))
            if model_source is None:
                sources.pop("model_name", None)
            else:
                locations = model_source if isinstance(model_source, list) else [model_source]
                for location in locations:
                    location.setdefault("batch_id", old.batch_id)
                sources["model_name"] = locations if isinstance(model_source, list) else locations[0]
            changes["source_reference"] = sources or None
            batch = tx.get("import_batch", {"batch_id": value.batch_id})
            self._check_sources(tx, batch, changes["source_reference"])
        changes["group_id"] = representative if has_pm else value.group_id
        snapshot = tx.update("equipment_snapshot", {"snapshot_id": snapshot_id}, changes)
        return self.repository.load(tx, equipment, snapshot)

    def _check_sources(self, tx, batch, reference):
        """Excel 출처의 시트와 셀이 보관된 원본에 존재하는지 검사한다.

        MANUAL 입력은 검사를 생략하며, 원본 셀의 값은 변경하지 않는다.

        param
        - tx: 명시적인 과거 원본 입력 작업을 확인할 현재 트랜잭션.
        - batch: 입력 방식과 원본 셀 값을 포함한 import_batch 행.
        - reference: 형식을 검증한 필드별 원본 위치. 위치의 batch_id가 없으면 batch를 사용.

        raises
        - StorageValidationError: Excel 출처가 없거나 원본에 없는 시트·셀을 참조하는 경우.
        """
        if batch["source_type"] != "EXCEL" and not reference:
            return
        if batch["source_type"] == "EXCEL":
            require(bool(reference), "Excel 자료에는 원본 셀 위치가 필요합니다.")
        for value in reference.values():
            for location in value if isinstance(value, list) else [value]:
                source_batch = (tx.get("import_batch", {"batch_id": location["batch_id"]})
                                if "batch_id" in location else batch)
                if source_batch["source_type"] != "EXCEL" and "batch_id" not in location:
                    continue
                require(source_batch["source_type"] == "EXCEL", "원본 셀의 입력 작업이 Excel 자료가 아닙니다.")
                raw = source_batch["raw_data"]
                cells = raw.get(location["sheet"])
                require(isinstance(cells, dict) and all(cell in cells for cell in location["cells"]),
                        "원본 입력 작업에 없는 시트·셀을 참조하고 있습니다.")

    def get_snapshot(self, equipment_code: str, base_date: date) -> StoredSnapshot | None:
        """호기 코드와 기준 날짜에 일치하는 설비 현황을 조회한다.

        기록이 없는 날짜는 이전 날짜나 현재 설비 정보로 채우지 않는다.

        param
        - equipment_code: 조회할 설비의 호기 코드.
        - base_date: 조회할 현황의 기준 날짜.

        return
        - SW·PM을 포함한 StoredSnapshot. 설비나 해당 날짜의 기록이 없으면 None.

        raises
        - StorageValidationError: 조회 조건이 잘못됐거나 저장된 PM의 SW 연결을 확인할 수 없는 경우.
        """
        required_text(equipment_code, "equipment_code", 50)
        require(type(base_date) is date, "base_date: 기준 날짜가 필요합니다.")
        with self.storage.transaction() as tx:
            equipment = self.repository.find_equipment(tx, equipment_code)
            if equipment is None:
                return None
            rows = self.repository.rows(tx, "equipment_snapshot", equipment_id=equipment["equipment_id"], base_date=base_date)
            return self.repository.load(tx, equipment, rows[0]) if rows else None

    def get_snapshot_sources(self, equipment_code: str, base_date: date, *, pm_name: str | None = None) -> tuple[EquipmentSourceCell, ...]:
        """현황 또는 고정 PM의 필드별 실제 입력 작업에서 원본 셀을 읽는다.

        일부 필드의 출처가 이전 입력 작업에 남아 있으면 위치의 batch_id를 우선한다.
        기록·출처가 없으면 빈 튜플이며 원본 셀이 없으면 available=False로 구분한다.
        """
        required_text(equipment_code, "equipment_code", 50)
        require(type(base_date) is date, "base_date: 기준 날짜가 필요합니다.")
        if pm_name is not None:
            required_text(pm_name, "pm_name", 50)
        with self.storage.transaction() as tx:
            equipment = self.repository.find_equipment(tx, equipment_code)
            if equipment is None:
                return ()
            snapshots = self.repository.rows(tx, "equipment_snapshot", equipment_id=equipment["equipment_id"], base_date=base_date)
            if not snapshots:
                return ()
            target = snapshots[0]
            if pm_name is not None:
                pms = [pm for pm in self.repository.rows(tx, "equipment_pm", snapshot_id=target["snapshot_id"])
                       if pm["pm_name"] == pm_name]
                if not pms:
                    return ()
                target = pms[0]
            batches, result = {}, []
            for field, locations in (target["source_reference"] or {}).items():
                for location in locations if isinstance(locations, list) else [locations]:
                    batch_id = location.get("batch_id", target["batch_id"])
                    if batch_id not in batches:
                        batches[batch_id] = tx.get("import_batch", {"batch_id": batch_id})
                    cells = (batches[batch_id]["raw_data"] or {}).get(location["sheet"], {})
                    for address in location["cells"]:
                        available = isinstance(cells, dict) and address in cells
                        result.append(EquipmentSourceCell(field=field, batch_id=batch_id,
                            sheet=location["sheet"], address=address,
                            value=cells[address] if available else None, available=available))
            return tuple(result)
