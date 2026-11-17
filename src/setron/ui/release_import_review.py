"""가져오기 전체 교체의 사용자 매칭·수정값·위험 복원 선택."""

from dataclasses import replace

from PySide6.QtWidgets import QMessageBox

from setron.features.release.replacement_schemas import ReplacementReview
from setron.ui.import_dialogs import choose_import_option, import_question


def choose_release_reviews(parent, plans, reviews=(), *, risk_decisions=None):
    """현재 단계의 명시적인 선택만 받고 다음 검토가 필요한지 알린다.

    param
    - parent: 대화상자의 부모 화면.
    - plans: DB에서 읽은 계열별 ReleaseImportPlan 튜플.
    - reviews: 이미 선택한 검토 결과. 처음에는 빈 튜플.
    - risk_decisions: 이번 저장 중 이미 확인한 위험 ID 집합. 거절도 반복 질문하지 않음.

    return
    - (선택 튜플, 추가 검토 필요 여부). 취소하면 (None, False).
    """
    chosen = list(reviews) if reviews else [ReplacementReview(fingerprint=p.preview.fingerprint) for p in plans]
    risk_decisions = risk_decisions if risk_decisions is not None else set()
    for index, plan in enumerate(plans):
        review, preview = chosen[index], plan.preview
        if preview.questions:
            releases, items = dict(review.release_matches), dict(review.item_matches)
            for question in preview.questions:
                labels = plan.release_labels if question.kind == "release" else plan.item_labels
                choices = ["기존 항목과 다른 새 항목", *(f"{labels.get(i, '미확인')} (ID {i})" for i in question.candidates)]
                value, ok = choose_import_option(parent, "같은 항목인지 확인",
                    f"계열: {plan.family_name}\n새 항목: {_entry_label(plan, question.kind, question.key)}\n{question.reason}",
                    choices)
                if not ok:
                    return None, False
                selected = choices.index(value)
                (releases if question.kind == "release" else items)[question.key] = (
                    question.candidates[selected - 1] if selected else None)
            chosen[index] = replace(review, release_matches=releases, item_matches=items)
            return tuple(chosen), True
        if preview.conflicts or preview.link_conflicts:
            choices, links = dict(review.choices), dict(review.link_choices)
            for conflict in preview.conflicts:
                selected = _pick_value(parent, plan.family_name, _entry_label(plan, conflict.kind, conflict.key),
                    conflict.field, conflict.current, conflict.incoming)
                if selected is None:
                    return None, False
                choices[(conflict.kind, conflict.key, conflict.field)] = selected
            for conflict in preview.link_conflicts:
                selected = _pick_value(parent, plan.family_name,
                    _entry_label(plan, "release", conflict.release_key) + " / " + _entry_label(plan, "item", conflict.item_key),
                    "Item 포함 상태 · 적용 모델 범위", _link_label(conflict.current, plan.model_labels),
                    _link_label(conflict.incoming, plan.model_labels))
                if selected is None:
                    return None, False
                links[(conflict.release_key, conflict.item_key)] = selected
            chosen[index] = replace(review, choices=choices, link_choices=links)
            return tuple(chosen), True
        confirmed = set(review.risk_confirmations)
        for proposal in preview.risk_reconnections:
            if not proposal.can_restore:
                confirmed.discard(proposal.risk_rule_id)
                if proposal.risk_rule_id in risk_decisions:
                    continue
                answer = import_question(parent, "수동 위험 지정 확인 필요",
                    f"계열: {plan.family_name}\n위험 지정 ID: {proposal.risk_rule_id}\n"
                    f"위험 사유: {plan.risk_labels.get(proposal.risk_rule_id, '확인 필요')}\n"
                    f"{_risk_evidence(proposal.target_reference)}\n"
                    f"확인할 내용: {proposal.reason or '같은 위험 대상을 확인할 수 없습니다.'}\n\n"
                    "위험 지정과 기존 근거는 미확인으로 보존합니다. 원본과 위험 대상을 다시 확인해 주세요.\n"
                    "미확인 상태로 저장을 계속할까요?",
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if answer != QMessageBox.StandardButton.Ok:
                    return None, False
                risk_decisions.add(proposal.risk_rule_id)
                continue
            if not proposal.requires_confirmation or proposal.risk_rule_id in risk_decisions:
                continue
            answer = import_question(parent, "수동 위험 지정 재연결 확인",
                f"계열: {plan.family_name}\n위험 지정 ID: {proposal.risk_rule_id}\n"
                f"사유: {plan.risk_labels.get(proposal.risk_rule_id, '확인 필요')}\n"
                f"{_risk_targets(plan, proposal)}\n{_risk_evidence(proposal.target_reference)}\n"
                "같은 대상으로 확인하여 다시 연결할까요? 아니오를 선택하면 미확인으로 보존합니다.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Cancel:
                return None, False
            risk_decisions.add(proposal.risk_rule_id)
            if answer == QMessageBox.StandardButton.Yes:
                confirmed.add(proposal.risk_rule_id)
        chosen[index] = replace(review, risk_confirmations=tuple(sorted(confirmed)))
    return tuple(chosen), False


def _entry_label(plan, kind, key):
    """요청 내부 키 대신 실제 버전 원문·Item 번호·내용을 표시한다.

    param
    - plan, kind, key: 계열 검토 자료와 항목 종류·요청 키.

    return
    - 사용자가 같은 항목인지 확인할 수 있는 원문 설명.
    """
    if kind == "release":
        return next(entry.value.version_raw for entry in plan.request.releases if entry.key == key)
    item = next(entry.value for entry in plan.request.items if entry.key == key)
    return f"Item {item.item_no} · {item.content}"


def _link_label(link, model_labels=None):
    """포함 상태와 모델 범위를 하나의 선택 단위로 설명한다.

    param
    - link: 확인할 ReplacementLink.
    - model_labels: 등록 모델 ID별 표시 이름. 없으면 확인이 필요한 ID로 표시.

    return
    - 상태·범위·지정 모델 ID 설명.
    """
    states = {"APPLIED": "적용", "NOT_APPLIED": "미적용", "UNKNOWN": "미확인"}
    models = ", ".join((model_labels or {}).get(i, f"모델 {i}") for i in link.target_group_ids)
    scopes = {"ALL": "전체 모델", "LISTED": f"지정 모델: {models}", "UNKNOWN": "모델 범위 미확인"}
    return f"{states[link.item_state.value]} / {scopes[link.target_scope.value]}"


def _pick_value(parent, family, entry, field, current, incoming):
    """현재 관리값과 새 자료 중 하나를 사용자가 직접 선택한다.

    param
    - parent, family, entry: 부모 화면과 계열·항목 이름.
    - field, current, incoming: 충돌 항목과 두 값.

    return
    - CURRENT 또는 INCOMING. 취소하면 None.
    """
    fields = {"version_raw": "버전 원문", "version_suffix": "파생 버전 표식", "released_at": "출시일",
              "version_order": "확인된 릴리즈 순서", "item_mapping_status": "Item 포함 관계 확인 상태",
              "item_no": "Item 원본 번호", "content": "업데이트 내용"}
    options = [f"현재 관리값 유지: {current}", f"새 자료 사용: {incoming}"]
    value, ok = choose_import_option(parent, "수정값 충돌 선택",
        f"계열: {family}\n항목: {entry}\n확인할 값: {fields.get(field, field)}", options)
    return ("CURRENT" if options.index(value) == 0 else "INCOMING") if ok else None


def _risk_targets(plan, proposal):
    """복원 요청 키를 실제 버전·Item 설명으로 변환한다.

    param
    - plan, proposal: 계열 검토 자료와 위험 복원 후보.

    return
    - 사용자가 확인할 후보 설명.
    """
    labels = {"release_version_id": "위험 버전", "start_release_version_id": "위험 시작 버전",
              "end_release_version_id": "위험 해제 버전", "item_id": "대상 Item"}
    return "\n".join(f"{labels[field]}: {_entry_label(plan, 'item' if field == 'item_id' else 'release', key)}"
                     for field, key in proposal.proposed_targets.items() if key is not None)


def _risk_evidence(reference):
    """보존한 원문과 원본 위치를 사용자에게 읽을 수 있는 근거로 표시한다.

    param
    - reference: 교체 전 보존한 수동 위험 근거.

    return
    - 원본 버전·Item 번호·내용·시트/셀 설명. 근거가 없으면 미확인 표시.
    """
    result = []
    for key, title in (("direct_version", "기존 위험 버전"), ("start_version", "기존 시작 버전"),
                       ("end_version", "기존 해제 버전"), ("item", "기존 Item")):
        value = reference.get(key)
        if not isinstance(value, dict):
            continue
        description = f"번호 {value.get('item_no')} · {value.get('content', '')}" if key == "item" else value.get("version_raw", "미확인")
        result.append(f"{title}: {description}")
        for locations in (value.get("source_reference") or {}).values():
            for location in locations if isinstance(locations, list) else (locations,):
                result.append(f"원본: {location.get('sheet', '미확인')} · {', '.join(location.get('cells', ())) }")
    return "\n".join(dict.fromkeys(result)) if result else "기존 원본 근거: 미확인"
