"""위험 지정(`risk_rule`)과 버전별 Item 상태(`release_version_item`)를 DB에서 읽는다 (명세 BE-015).

판정에 필요한 값만 옮기고 바꾸지 않는다. 재연결 근거(`target_reference`)는 판정에 쓰지 않아 읽지 않는다.
저장·수정은 위험 기준 등록 화면과 공통 저장 서비스(`features/storage`)의 몫이다.
"""

from __future__ import annotations

from collections.abc import Mapping

from setron.features.equipment.repository import EquipmentRepository
from setron.features.item.schemas import ItemState, TargetScope, VersionItem
from setron.features.item.service import ItemCatalog
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.features.risk.service import RiskAssessor
from setron.features.version.service import FamilyReleases


class RiskRepository:
    """``storage``는 ``transaction()``을 제공하는 공통 저장 서비스다."""

    def __init__(self, storage) -> None:
        self.storage = storage
        self._rows = EquipmentRepository().rows

    def load_rules(self, tx=None) -> tuple[RiskRule, ...]:
        """등록된 위험 지정 전체. 사용하지 않는 지정도 그대로 읽고, 판정에서 빼는 일은 :class:`RiskAssessor`가 한다."""
        if tx is None:
            with self.storage.transaction() as tx:
                return self.load_rules(tx)
        return tuple(
            RiskRule(
                row["risk_rule_id"], RuleType(row["rule_type"]), row["family_id"], row["risk_reason"],
                group_id=row["group_id"], item_id=row["item_id"], release_version_id=row["release_version_id"],
                start_release_version_id=row["start_release_version_id"],
                end_release_version_id=row["end_release_version_id"], is_enabled=bool(row["is_enabled"]),
                mapping_status=MappingStatus(row["mapping_status"]),
            )
            for row in self._rows(tx, "risk_rule")
        )

    def load_version_items(self, tx=None) -> tuple[VersionItem, ...]:
        """저장된 버전별 Item 상태 전체 (규칙 H: 저장된 행만 쓴다)."""
        if tx is None:
            with self.storage.transaction() as tx:
                return self.load_version_items(tx)
        return tuple(
            VersionItem(row["release_version_id"], row["item_id"], ItemState(row["item_state"]),
                        TargetScope(row["target_scope"]), tuple(row["target_group_ids"] or ()))
            for row in self._rows(tx, "release_version_item")
        )

    def assessor(self, families: Mapping[int, FamilyReleases], tx=None) -> RiskAssessor:
        """DB의 위험 지정과 Item 상태로 판정기를 만든다."""
        if tx is None:
            with self.storage.transaction() as tx:
                return self.assessor(families, tx)
        catalog = ItemCatalog(families, self.load_version_items(tx))
        return RiskAssessor(families, catalog, self.load_rules(tx))
