"""가져오기 초안을 동일 사업장·시트의 이전 관측과 물리 설비 단위로 비교한다."""

import json
from collections import defaultdict

from sqlalchemy import select

from setron.features.data_admin.service import sheet_names
from setron.features.excel_import.reference import ReferenceData, resolve_family
from setron.features.excel_import.service import parse_date
from setron.features.excel_import.schemas import ImportComparison, ImportDraft
from setron.features.storage.service import StorageService


class ComparisonScopeError(ValueError):
    pass


def _stored_text(value):
    return bytes(value).decode("utf-8") if isinstance(value, (bytes, bytearray, memoryview)) else value


def compare_import(storage: StorageService, draft: ImportDraft, reference: ReferenceData) -> ImportComparison | None:
    current = defaultdict(set)
    scopes = set()
    for row in draft.active_rows:
        site, code, version = (row.value(key) for key in ("site", "equipment_code", "version_raw"))
        try:
            day = parse_date(row.value("base_date") or "")
        except ValueError:
            raise ComparisonScopeError("비교할 기준일을 입력해 주세요.") from None
        if not site or not code or not version:
            raise ComparisonScopeError("사업장·설비 호기·버전을 확인한 뒤 비교할 수 있습니다.")
        family = resolve_family(row, draft.kind, reference, sheet_name=draft.sheet.sheet_name,
                                workbook_name=draft.workbook_name)
        if family is None:
            raise ComparisonScopeError("SW 계열을 확인한 뒤 직전 저장본과 비교할 수 있습니다.")
        scopes.add((site, day))
        module = row.value("pm_name") or ""
        current[code].add((module if module.strip() else "", family.releases.family_id, version))
    if len(scopes) != 1:
        raise ComparisonScopeError("같은 기준일·사업장의 행을 선택해야 직전 저장본과 비교할 수 있습니다.")
    site, day = scopes.pop()
    sheet = draft.sheet.sheet_name
    with storage.transaction() as tx:
        table = tx.repository.table
        snap, batch = table("equipment_snapshot"), table("import_batch")
        candidates = (select(snap.c.base_date, snap.c.batch_id).distinct()
                      .select_from(snap.join(batch, batch.c.batch_id == snap.c.batch_id))
                      .where(snap.c.site == site, snap.c.base_date < day, batch.c.source_type == "EXCEL")
                      .order_by(snap.c.base_date.desc()))
        matching_ids = []
        previous_date = None
        matching_sheet = {}
        for candidate_day, batch_id in tx.session.execute(candidates).all():
            if previous_date is not None and candidate_day != previous_date:
                break
            if batch_id not in matching_sheet:
                raw = tx.session.scalar(select(batch.c.raw_data).where(batch.c.batch_id == batch_id))
                raw = json.loads(raw) if isinstance(raw, str) else raw
                # 파일 이름 키(WORKBOOK_KEY)는 시트가 아니므로 세지 않는다.
                matching_sheet[batch_id] = (isinstance(raw, dict) and len(sheet_names(raw)) == 1
                                            and isinstance(raw.get(sheet), dict))
            if matching_sheet[batch_id]:
                matching_ids.append(batch_id)
                previous_date = candidate_day
        if previous_date is None:
            return None
        eq, sw, pm = (table(name) for name in ("equipment", "equipment_version", "equipment_pm"))
        query = (select(eq.c.equipment_code, pm.c.pm_name, sw.c.family_id, sw.c.version_raw)
                 .select_from(snap.join(eq, eq.c.equipment_id == snap.c.equipment_id)
                              .outerjoin(sw, sw.c.snapshot_id == snap.c.snapshot_id)
                              .outerjoin(pm, pm.c.equipment_version_id == sw.c.equipment_version_id))
                 .where(snap.c.site == site, snap.c.base_date == previous_date, snap.c.batch_id.in_(matching_ids)))
        previous = defaultdict(set)
        for code, module, family_id, version in tx.session.execute(query):
            previous[_stored_text(code)].add((_stored_text(module) or "", family_id, _stored_text(version)))
    common = current.keys() & previous.keys()
    changed = sorted(code for code in common if current[code] != previous[code])
    details = ()
    if changed:
        suffix = f" 외 {len(changed) - 10}대" if len(changed) > 10 else ""
        details = ("버전 변경 설비: " + ", ".join(changed[:10]) + suffix,)
    return ImportComparison(previous_date, f"{site} · {sheet}", len(changed), len(current.keys() - previous.keys()),
                            len(common) - len(changed), len(previous.keys() - current.keys()), details)
