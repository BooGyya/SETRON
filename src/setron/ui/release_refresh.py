"""릴리즈 저장 화면에 현재 선택 조건과 DB 저장 여부를 전달한다."""

from setron.features.import_save.service import DatabaseImportSaver
from setron.features.release.refresh_schemas import RefreshContext


def capture_release_context(source, state) -> RefreshContext:
    """적용된 목표 N을 보존하고 저장 시점의 모델 적용 근거 재조회를 요청한다."""
    if not hasattr(source, "applicability"):
        return RefreshContext(use_saved_applicability=True)
    selected = state.applied.targets if state.applied is not None else state.target_draft
    chosen = dict(source.targets) if selected is None else {
        (target.model_id, target.family_id): target.release_id for target in selected}
    for record in source.records:
        if not record.is_hpa:
            for pm in record.pms:
                chosen.setdefault((pm.group_id, pm.family_id), None)
    return RefreshContext(targets=chosen, use_saved_applicability=True)


def is_database_import_saver(saver) -> bool:
    """실제 DB 저장 성공에만 기존 조회 결과를 무효화한다."""
    return isinstance(saver, DatabaseImportSaver)
