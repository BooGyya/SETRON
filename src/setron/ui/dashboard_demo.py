"""대시보드 동작 확인 전용 합성 자료. 실제 회사 설비/목표/위험 근거가 아니다."""

from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.query_state import preview_catalog


def demo_dashboard_source() -> DashboardSource:
    catalog = preview_catalog()
    bindings = tuple(GroupBinding(group.key, (101 + i,)) for i, group in enumerate(catalog.groups))
    families = {1001 + i: FamilyReleases(1001 + i, tuple(
        ReleaseVersion((1001 + i) * 10 + step, 1001 + i, f"v1.0.{step}", step)
        for step in (1, 2, 3))) for i in range(len(bindings))}
    targets = {(101 + i, 1001 + i): (1001 + i) * 10 + 2 for i in range(len(bindings))}
    areas = (("pyeongtaek", "P1"), ("pyeongtaek", "P2"), ("pyeongtaek", "P3"), ("pyeongtaek", "P4"),
             ("hwaseong", "15L"), ("hwaseong", "16L"), ("hwaseong", "NRD"), ("giheung", "5L"))
    sites = {o.key: o.label for o in catalog.sites}
    locations, risks, records = {}, {}, []
    eid = 0
    for area_index, (site, area) in enumerate(areas):
        for i, group in enumerate(catalog.groups):
            if (area_index + i) % 5 == 0 and i > 0:
                continue
            eid += 1
            locations[eid] = EquipmentLocation(site, area)  # Fab/Line은 모름. 원본 구역으로 추론하지 않음
            risks[eid] = RiskStatus.RISK if eid % 11 == 0 else RiskStatus.UNKNOWN if eid % 7 == 0 else RiskStatus.NO_RISK
            for month in range(5, 11):
                if month == 7 and eid % 13 == 0:
                    continue  # 관측 공백: 이전 값으로 채우면 안 됨
                step = 2 if (eid % 7) < month - 4 else 1
                raw = None if eid % 9 == 0 else f"v1.0.{step}"
                pms = (PmVersion(None, 101 + i, 1001 + i, raw),)
                if eid == 1:
                    pms = (PmVersion("PM1", 101, 1001, raw), PmVersion("PM2", 101, 1001, "v1.0.2"),
                           PmVersion("PM3", 104, 1004, "v1.0.2"))  # 복수 SW/PM이어도 물리 설비 1대
                records.append(EquipmentRecord(eid, f"DEMO-{eid:03}", date(2026, month, 21), sites[site],
                                               None, group.label, pms))
    for site in sites:
        eid += 1
        locations[eid] = EquipmentLocation(site)
        for month in range(5, 11):
            records.append(EquipmentRecord(eid, f"DEMO-HPA-{eid}", date(2026, month, 21), sites[site],
                                           None, "HPA", (), is_hpa=True))
    return DashboardSource(catalog=catalog, bindings=bindings, records=tuple(records), locations=locations,
                           families=families, targets=targets, risks=risks,
                           source_label="합성 예시 데이터 · 실데이터/DB 미연결 · 위험 후보·목표 N도 예시")
