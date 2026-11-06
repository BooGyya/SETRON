"""관리 화면의 이름·형식과 워커에서 실행하는 DB 경계."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path

from setron.features.equipment.repository import EquipmentRepository
from setron.features.management import ManagementService
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.storage.service import StorageService
from setron.infrastructure.database import Database, DatabaseConfig


KINDS = (
    ("equipment", "설비 호기"), ("equipment_snapshot", "날짜별 설비 현황"),
    ("equipment_pm", "PM 구성 · SW"), ("equipment_version", "일반 설비 SW"),
    ("release_version", "릴리즈"), ("item", "Item 내용"),
    ("release_version_item", "릴리즈별 Item 적용"), ("risk_rule", "위험 정보"),
    ("app_setting", "노후도 기간 설정"),
)
FIELD_NAMES = {
    "equipment_code": "설비 호기", "line": "라인", "model_name": "모델 표기",
    "group_id": "모델", "production_area": "생산 영역", "structure_type": "설비 구조",
    "geometry_type": "형상", "modification_type": "개조 유형", "family_id": "버전 계열",
    "version_raw": "SW 버전", "release_version_id": "대응 릴리즈", "model_name_raw": "PM 모델 표기",
    "version_suffix": "핫픽스 표기", "released_at": "출시일", "version_order": "확인된 릴리즈 순서",
    "item_mapping_status": "Item 매핑 확인", "item_no": "원본 Item 번호", "content": "변경 내용",
    "item_state": "Item 적용 상태", "target_scope": "적용 모델 범위", "target_group_ids": "적용 모델",
    "rule_type": "위험 유형", "item_id": "대상 Item", "start_release_version_id": "위험 시작 버전",
    "end_release_version_id": "위험 해제 버전", "risk_reason": "위험 사유",
    "is_enabled": "위험 지정 사용", "mapping_status": "위험 연결 확인", "setting_value": "기간 기준",
}
ENUMS = {
    "production_area": ((None, "미확인"), ("MEMORY", "Memory"), ("FOUNDRY", "Foundry")),
    "item_mapping_status": (("UNKNOWN", "미확인"), ("VERIFIED", "확인됨")),
    "item_state": (("APPLIED", "적용"), ("NOT_APPLIED", "미적용"), ("UNKNOWN", "미확인")),
    "target_scope": (("ALL", "계열 전체"), ("LISTED", "지정 모델"), ("UNKNOWN", "미확인")),
    "rule_type": (("SAFETY_REQUIRED", "환경 · 안전 필수 Item"), ("DEFECT_ITEM", "결함 Item"),
                  ("DIRECT_VERSION", "위험 버전 직접 지정")),
    "mapping_status": (("VERIFIED", "확인됨"), ("UNRESOLVED", "연결 미확인")),
}
OPTIONAL_TEXT = {"line", "structure_type", "geometry_type", "modification_type", "model_name_raw", "version_suffix"}
OPTIONAL_IDS = {"group_id", "item_id", "release_version_id", "start_release_version_id", "end_release_version_id"}


@dataclass(frozen=True)
class ManagementRows:
    records: tuple
    references: dict
    offset: int = 0
    has_more: bool = False


class ManagementGateway:
    """화면은 이 동기 경계를 항상 워커에서 호출한다. 저장할 때 받은 조회 조건만 사용한다."""

    def __init__(self, storage):
        self.storage = storage

    @contextmanager
    def _storage(self):
        yield self.storage

    def load(self, table, offset=0, limit=100):
        with self._storage() as storage:
            service = ManagementService(storage)
            filters = {"setting_key": "version_age_thresholds"} if table == "app_setting" else None
            records = service.list_records(table, limit=limit + 1, offset=offset, filters=filters)
            with storage.transaction() as tx:
                repository = EquipmentRepository()
                references = {name: tuple(repository.rows(tx, name)) for name in (
                    "equipment", "equipment_snapshot", "equipment_group", "version_family", "release_version", "item",
                    "release_version_item",
                )}
            return ManagementRows(records[:limit], references, offset, len(records) > limit)

    def get(self, table, key):
        with self._storage() as storage:
            return ManagementService(storage).get_record(table, key)

    def save(self, edits, context=RefreshContext(), applicability=()):
        with self._storage() as storage:
            return ManagementService(storage, context).save(edits, applicability=applicability)

    def get_applicability(self, model_id, family_id):
        with self._storage() as storage:
            return ManagementService(storage).get_applicability(model_id, family_id)

    def create_risk_rule(self, values, context=RefreshContext()):
        """위험 기준을 새로 등록하고 재판정한 결과를 돌려준다 (BE-015). 저장 결과 형식은 ``save``와 같다."""
        with self._storage() as storage:
            return ManagementService(storage, context).create_risk_rule(values)

    def preview_risk_rule(self, values, context=RefreshContext()):
        """위험 기준을 저장하지 않고 등록했을 때의 영향(릴리즈·설비)을 돌려준다 (BE-015). 값 형식은 ``create_risk_rule``과 같다."""
        with self._storage() as storage:
            return ManagementService(storage, context).preview_risk_rule(values)

    def version_order_candidates(self, family_id):
        """계열의 릴리즈 기록과 릴리즈노트 나열 순서 후보(데이터 파트 규칙)를 함께 돌려준다. 저장하지 않는다."""
        from setron.features.release.order_candidates import suggest_version_orders

        with self._storage() as storage:
            records = ManagementService(storage).list_records("release_version", limit=1000,
                                                              filters={"family_id": family_id})
        return records, suggest_version_orders(family_id, [record.current for record in records])

    def export_management(self, path):
        from setron.features.export.management_tables import save_management_data
        from setron.features.export.schemas import ExportResult, ExportStatus
        from setron.infrastructure.database import DatabaseError
        try:
            with self._storage() as storage:
                return save_management_data(storage, path)
        except DatabaseError:
            return ExportResult(ExportStatus.FAILED, Path(path), "관리 자료를 읽지 못했습니다. DB 연결을 확인해 주세요.")

    def export_status(self, path, base_date, context, filters=None):
        from setron.features.export.service import save_status_report
        from setron.features.export.schemas import ExportResult, ExportStatus
        from setron.infrastructure.database import DatabaseError
        try:
            with self._storage() as storage:
                report = ManagementService(storage, context).current_report(base_date, filters=filters)
                return save_status_report(report, path)
        except (DatabaseError, ValueError):
            return ExportResult(ExportStatus.FAILED, Path(path), "조회 조건의 현황을 출력하지 못했습니다. 조건과 DB 연결을 확인해 주세요.")


class EnvironmentManagementGateway(ManagementGateway):
    """설정만 보관하고 각 워커 호출의 연결과 세션을 호출 종료 시 정리한다."""

    def __init__(self, environ):
        self.environ = dict(environ)
        DatabaseConfig.from_env(self.environ)

    @contextmanager
    def _storage(self):
        database = Database.from_env(self.environ)
        try:
            yield StorageService(database)
        finally:
            database.dispose()


def gateway_from_env(environ=None):
    values = os.environ if environ is None else environ
    return EnvironmentManagementGateway(values) if values.get("SETRON_DB_NAME") else None


def reference_options(field, references, *, family_id=None):
    """식별자는 선택값으로 보관하고 모델명·버전·Item 내용으로 표시한다."""
    if field in {"group_id", "target_group_ids"}:
        return tuple((row["group_id"], f"{row['group_name']} · {row['category']}")
                     for row in references.get("equipment_group", ()))
    if field == "family_id":
        ui_names = {"LEGACY": "구UI", "INTEGRATED": "통합UI", "SMART": "스마트UI", None: "UI 구분 없음"}
        return tuple((row["family_id"], f"{row['family_name']} · {ui_names.get(row.get('ui_type'), 'UI 확인 필요')}")
                     for row in references.get("version_family", ()))
    if field == "item_id":
        return tuple((row["item_id"], item_title(row, references))
                     for row in references.get("item", ()))
    if field.endswith("release_version_id"):
        families = {row["family_id"]: row["family_name"] for row in references.get("version_family", ())}
        return tuple((row["release_version_id"], f"{families.get(row['family_id'], '계열 미확인')} · {row['version_raw']}")
                     for row in references.get("release_version", ()) if family_id is None or row["family_id"] == family_id)
    return ()


def item_title(row, references):
    """반복되는 Item 번호는 연결 릴리즈와 원본 위치로 구분한다."""
    releases = {value["release_version_id"]: value for value in references.get("release_version", ())}
    families = {value["family_id"]: value["family_name"] for value in references.get("version_family", ())}
    linked = []
    for link in references.get("release_version_item", ()):
        release = releases.get(link["release_version_id"])
        if link["item_id"] == row["item_id"] and release is not None:
            linked.append(f"{families.get(release['family_id'], '계열 미확인')} {release['version_raw']}")
    context = ", ".join(dict.fromkeys(linked[:3]))
    if len(linked) > 3:
        context += f" 외 {len(linked) - 3}개"
    location = ""
    for locations in (row.get("source_reference") or {}).values():
        first = locations[0] if isinstance(locations, list) and locations else locations
        if isinstance(first, dict) and first.get("cells"):
            location = f"{first['sheet']}!{first['cells'][0]}"
            break
    details = " · ".join(value for value in (context, location) if value)
    text = f"Item {row.get('item_no') if row.get('item_no') is not None else '번호 없음'} · {row['content'][:80]}"
    return text + (f" · {details}" if details else "")


def display_value(field, value, references):
    if value is None:
        return "미확인 / 값 없음"
    if field in ENUMS:
        return dict(ENUMS[field]).get(value, str(value))
    if field == "is_enabled":
        return "사용" if value else "사용 안 함"
    if field == "setting_value" and isinstance(value, dict):
        unit = {"YEAR": "년", "MONTH": "개월", "DAY": "일"}.get(value.get("unit"), "단위 미확인")
        return f"주의 {value.get('warning', '미확인')}{unit} · 위험 {value.get('danger', '미확인')}{unit}"
    if field == "target_group_ids":
        names = dict(reference_options(field, references))
        return ", ".join(names.get(key, "등록 모델 미확인") for key in value)
    choices = dict(reference_options(field, references))
    if choices:
        return choices.get(value, "등록 자료에서 찾을 수 없음")
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value != "" else "(빈 값)"


def record_title(record, references):
    value = record.current
    equipment = {row["equipment_id"]: row["equipment_code"] for row in references.get("equipment", ())}
    snapshots = {row["snapshot_id"]: row for row in references.get("equipment_snapshot", ())}
    snapshot = snapshots.get(value.get("snapshot_id"), value)
    context = f"{equipment.get(snapshot.get('equipment_id'), '설비')} · {snapshot.get('base_date', '')}"
    if record.table == "equipment":
        return value["equipment_code"]
    if record.table == "equipment_snapshot":
        return f"{context} · {value['site']} · {value['model_name']}"
    if record.table == "equipment_pm":
        return f"{context} · {value['pm_name']} · {value['version_raw']}"
    if record.table == "equipment_version":
        return f"{context} · {display_value('family_id', value['family_id'], references)} · {value['version_raw']}"
    if record.table == "release_version":
        return f"{display_value('family_id', value['family_id'], references)} · {value['version_raw']}"
    if record.table == "item":
        return item_title(value, references)
    if record.table == "release_version_item":
        return f"{display_value('release_version_id', value['release_version_id'], references)} · {display_value('item_id', value['item_id'], references)}"
    if record.table == "risk_rule":
        return f"{display_value('rule_type', value['rule_type'], references)} · {value['risk_reason'][:100]}"
    return "노후도 주의 · 위험 기간 기준"


def safe_message(error):
    """DB 쿼리나 연결 정보 대신 입력 오류와 고정 업무 안내만 보여 준다."""
    from setron.features.storage.validation import StorageValidationError
    from setron.features.aging.schemas import ThresholdError
    from setron.infrastructure.database import DatabaseError
    if isinstance(error, (StorageValidationError, ThresholdError, LookupError)):
        message = str(error)
        for key, name in sorted(FIELD_NAMES.items(), key=lambda item: -len(item[0])):
            message = message.replace(key, name)
        for key, name in KINDS:
            message = message.replace(key + ".", name + " · ").replace(key + ":", name + ":")
        return message
    if isinstance(error, DatabaseError):
        return "DB 작업을 완료하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요."
    return "작업을 완료하지 못했습니다. 입력 내용과 연결 상태를 확인해 주세요."
