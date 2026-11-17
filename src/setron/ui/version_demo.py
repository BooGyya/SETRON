"""B-1 확인 전용 합성 Item·문서 위치. 실제 Excel 파일이나 회사 자료가 아니다."""

from dataclasses import replace

from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.ui.version_data import ItemEntry, SourceReference, VersionCatalog, catalog_from_dashboard


def demo_version_catalog(source):
    base = catalog_from_dashboard(source)
    entries, items, rows = [], [], []
    for fid, family in base.families().items():
        document = f"DEMO_계열{fid}_릴리즈.xlsx (합성 문서)"
        family_models = tuple(p.model_id for p in base.pairs if p.family_id == fid)
        for index, content in enumerate(("Recipe 승인 상태 확인", "환경안전 Interlock 추가", "운영 상태 표시 수정"), 1):
            item_id = fid * 10 + index
            items.append(ItemEntry(Item(item_id, index, content), (
                SourceReference("변경 내용", document, "예시 R1", "Release", f"D{index + 4}", fid),)))
        for position, release in enumerate(family.sorted.ordered):
            # 같은 ID·버전·순서를 재사용한다. 기존 자료에 없는 출시일은 미확인으로 보존한다.
            entry = base.entry(release.release_version_id)
            entries.append(replace(entry, sources=(
                SourceReference("버전 원문", document, "예시 R1", "Release", f"B{position + 2}", fid),
                SourceReference("확인된 순서", document, "예시 R1", "Release", f"A{position + 2}", fid),
            )))
            rows.append(VersionItem(release.release_version_id, fid * 10 + 1, ItemState.APPLIED, TargetScope.ALL))
            rows.append(VersionItem(release.release_version_id, fid * 10 + 2,
                                    ItemState.NOT_APPLIED if position == 0 else ItemState.APPLIED,
                                    TargetScope.LISTED, family_models))
            if position == 0:
                rows.append(VersionItem(release.release_version_id, fid * 10 + 3, ItemState.APPLIED, TargetScope.UNKNOWN))
            # 이후에는 누적되지만 모델 범위 UNKNOWN은 그대로여서 설비 적용은 미확인이다.
        entries.extend(base.entry(r.release_version_id) for r in family.sorted.unordered)
    return VersionCatalog(base.pairs, tuple(entries), tuple(items), tuple(rows),
                          "합성 예시 데이터 · DB 미연결 · 문서명·셀 주소도 예시")
