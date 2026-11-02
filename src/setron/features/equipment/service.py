"""검토한 설비 현황 한 묶음을 저장한다. 재가져오기·수정 정책은 BE-009/025에서 다룬다."""

from dataclasses import fields
from datetime import date

from .repository import EquipmentRepository
from .schemas import SnapshotInput, StoredSnapshot
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
        require(isinstance(snapshots, tuple) and bool(snapshots), "저장할 현황 묶음이 필요합니다.")
        prepared = tuple(prepare_snapshot(snapshot) for snapshot in snapshots)
        keys = [(value.equipment_code, value.base_date) for value in prepared]
        require(len(set(keys)) == len(keys), "같은 호기·기준일의 현황이 중복됐습니다. 저장 전에 합쳐야 합니다.")
        with self.storage.transaction() as tx:
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
        batch = tx.get("import_batch", {"batch_id": value.batch_id})
        self._check_sources(batch, value.source_reference)
        group_ids = {pm.group_id for software in value.software for pm in software.pms}
        if value.group_id is not None:
            group_ids.add(value.group_id)
        groups = {group_id: tx.get("equipment_group", {"group_id": group_id}) for group_id in sorted(group_ids)}
        for software in value.software:
            self._check_sources(batch, software.source_reference)
            family = tx.get("version_family", {"family_id": software.family_id})
            owner = tx.get("equipment_group", {"group_id": family["group_id"]})
            models = {pm.group_id for pm in software.pms} or {value.group_id}
            require(all(groups[model]["category"] == owner["category"] for model in models),
                    "PM 모델과 버전 계열의 설비군이 다릅니다. KCTC O2와 Oxide O2를 구분해야 합니다.")
            if software.release_version_id is not None:
                release = tx.get("release_version", {"release_version_id": software.release_version_id})
                require(release["family_id"] == software.family_id, "적용 SW와 연결 릴리즈의 버전 계열이 다릅니다.")
            for pm in software.pms:
                self._check_sources(batch, pm.source_reference)
        equipment = self.repository.find_equipment(tx, value.equipment_code, lock=True)
        if equipment is None:
            equipment = tx.insert("equipment", {"equipment_code": value.equipment_code})
        existing = self.repository.rows(tx, "equipment_snapshot", equipment_id=equipment["equipment_id"], base_date=value.base_date)
        require(not existing, "같은 호기·기준일의 현황이 이미 있습니다. 재가져오기 검토가 필요합니다.")
        snapshot = tx.insert("equipment_snapshot", {
            **{field.name: getattr(value, field.name) for field in fields(SnapshotInput)
               if field.name not in {"equipment_code", "software"}},
            "equipment_id": equipment["equipment_id"],
        })
        for software in value.software:
            version = tx.insert("equipment_version", {
                "snapshot_id": snapshot["snapshot_id"], "family_id": software.family_id,
                "version_raw": software.version_raw, "release_version_id": software.release_version_id,
                "batch_id": value.batch_id, "source_reference": software.source_reference,
            })
            for pm in software.pms:
                tx.insert("equipment_pm", {
                    "snapshot_id": snapshot["snapshot_id"], "equipment_version_id": version["equipment_version_id"],
                    "pm_name": pm.pm_name, "group_id": pm.group_id, "model_name_raw": pm.model_name_raw,
                    "batch_id": value.batch_id, "source_reference": pm.source_reference,
                })
        return self.repository.load(tx, equipment, snapshot)

    @staticmethod
    def _check_sources(batch, reference):
        """Excel 출처의 시트와 셀이 보관된 원본에 존재하는지 검사한다.

        MANUAL 입력은 검사를 생략하며, 원본 셀의 값은 변경하지 않는다.

        param
        - batch: 입력 방식과 원본 셀 값을 포함한 import_batch 행.
        - reference: 형식을 검증한 필드별 원본 위치. 출처가 없으면 None.

        raises
        - StorageValidationError: Excel 출처가 없거나 원본에 없는 시트·셀을 참조하는 경우.
        """
        if batch["source_type"] != "EXCEL":
            return
        require(bool(reference), "Excel 자료에는 원본 셀 위치가 필요합니다.")
        raw = batch["raw_data"]
        for value in reference.values():
            for location in value if isinstance(value, list) else [value]:
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
