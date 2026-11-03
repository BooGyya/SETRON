"""모델별 적용 가능 릴리즈만 저장하고 목표 N은 조회 요청으로 유지한다 (BE-011).

app_setting에는 확인 근거와 릴리즈 지문만 보관한다. 모델·문서의 이름이나
접미사, 현재 설치된 버전으로 적용 범위 또는 릴리즈 순서를 추측하지 않는다.
"""

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
import hashlib
import json

from sqlalchemy import select

from setron.features.storage.inputs import identifier, merge_sources, require, required_text
from setron.features.storage.validation import StorageValidationError, decode_record
from setron.features.version.schemas import ReleaseVersion

from .schemas import CandidateConfirmation, ExcludedCandidate, TargetCandidate, TargetCatalog, TargetPair


class TargetService:
    """적용 후보 등록과 조회를 공통 저장 트랜잭션에서 수행한다."""

    def __init__(self, storage):
        self.storage = storage

    def confirm(self, model_id, family_id, candidates):
        """한 쌍의 적용 확인 목록을 교체한다. 목표 N은 저장하지 않는다."""
        with self.storage.transaction() as tx:
            return self.confirm_in(tx, model_id, family_id, candidates)

    def confirm_in(self, tx, model_id, family_id, candidates):
        """현재 트랜잭션에서 모델/계열/릴리즈를 검사하고 근거 목록을 저장한다.

        후보마다 적용 표식과 분기·순서를 확인한 근거, 출처를 명시해야 한다.
        알 수 없는 순서·중복 순서는 적용 확인을 보관하더라도 N 후보에서 제외한다.
        이후 릴리즈나 모델·계열 정의가 달라지면 재확인 전까지 사용하지 않는다.
        """
        pair = _pair((model_id, family_id))
        # BE-010의 계열 잠금 다음, BE-004와 동일하게 릴리즈→모델 순서로 잠근다.
        # 모델을 먼저 잡으면 save_item_links의 릴리즈→모델 잠금과 교착한다.
        families = _rows(tx, "version_family", lock=True, family_id=family_id)
        releases = {r["release_version_id"]: r for r in
                    _rows(tx, "release_version", lock=True, family_id=family_id)}
        models = _rows(tx, "equipment_group", lock=True)
        reason = _pair_reason(pair, models, families)
        require(reason is None, reason)
        setting = _setting(tx, pair, lock=True)
        values = tuple(candidates)
        require(all(isinstance(c, CandidateConfirmation) for c in values),
                "CandidateConfirmation 형식의 모델 적용 확인 근거가 필요합니다.")
        prepared = tuple(_confirmation(c) for c in values)
        require(len({c.release_id for c in prepared}) == len(prepared), "적용 릴리즈 ID가 중복됐습니다.")
        for candidate in prepared:
            require(candidate.release_id in releases, "같은 계열의 현재 릴리즈만 적용 확인할 수 있습니다.")
        payload = {
            "schema": 1, "model_id": model_id, "family_id": family_id,
            "pair_fingerprint": _pair_fingerprint(pair, models, families),
            "candidates": [{"confirmation": asdict(c), "fingerprint": _fingerprint(releases[c.release_id])}
                           for c in prepared],
        }
        key = {"setting_key": _key(pair)}
        if setting is None:
            tx.insert("app_setting", {**key, "setting_value": payload})
        else:
            tx.update("app_setting", key, {"setting_value": payload})
        return self.read_in(tx, (pair,)).pairs[0]

    def read(self, pairs, targets=None):
        """read_in과 같은 계약으로 새 읽기 트랜잭션을 연다."""
        with self.storage.transaction() as tx:
            return self.read_in(tx, pairs, targets)

    def read_in(self, tx, pairs, targets=None):
        """같은 관측 트랜잭션에서 모델·계열별 후보와 선택 N을 반환한다.

        targets=None이면 최신 확인 후보를 초기 선택으로 사용한다. mapping에
        포함된 쌍은 None/무효 ID까지 보존하고, 없는 쌍만 최신 후보로 초기화한다.
        추천값·선택값은 반환만 하며 저장하지 않는다. 조회 범위 밖 mapping도
        수정하지 않는다. 릴리즈 변경·삭제·교체 후에는 지문을 다시 검사한다.
        """
        requested = tuple(_pair(pair) for pair in pairs)
        require(len(set(requested)) == len(requested), "모델·계열 쌍이 중복됐습니다.")
        chosen = _targets(targets)
        models, families = _rows(tx, "equipment_group"), _rows(tx, "version_family")
        releases = _rows(tx, "release_version")
        result = []
        for pair in requested:
            reason = _pair_reason(pair, models, families)
            available, excluded, confirmed = (), (), False
            if reason is None:
                saved = _setting(tx, pair)
                available, excluded, confirmed, reason = _candidates(
                    pair, saved, releases, _pair_fingerprint(pair, models, families))
            recommended = available[-1].release_id if available else None
            selected = chosen[pair] if pair in chosen else recommended
            selection_reason = reason
            if selected is None:
                selection_reason = reason or "목표 N이 미지정입니다. 확인된 후보에서 선택해 주세요."
            elif selected not in {c.release_id for c in available}:
                specific = next((e.reason for e in excluded if e.release_id == selected), None)
                selection_reason = specific or reason or "선택한 N의 모델·계열 적용 근거를 다시 확인해 주세요."
            else:
                selection_reason = None
            result.append(TargetPair(*pair, available, recommended, selected,
                                     selected is not None and selection_reason is None,
                                     selection_reason, excluded, confirmed))
        return TargetCatalog(tuple(result))


def _pair(value):
    require(isinstance(value, tuple) and len(value) == 2, "모델·계열은 (model_id, family_id) 쌍이어야 합니다.")
    identifier(value[0], "model_id")
    identifier(value[1], "family_id")
    return value


def _targets(values):
    require(values is None or isinstance(values, Mapping), "선택 N은 모델·계열별 mapping이어야 합니다.")
    result = {}
    for pair, selected in (() if values is None else values.items()):
        pair = _pair(pair)
        if selected is not None:
            identifier(selected, "selected_release_id")
        result[pair] = selected
    return result


def _confirmation(value):
    identifier(value.release_id, "release_id")
    required_text(value.applicability_basis, "모델 적용 표식 근거", 10000)
    required_text(value.order_basis, "분기·순서 확인 근거", 10000)
    if value.source_text is not None:
        required_text(value.source_text, "원본 출처 설명", 10000)
    source = merge_sources(value.source_reference)
    require(value.source_text is not None or source is not None, "적용 확인의 출처 텍스트 또는 원본 셀 위치가 필요합니다.")
    return CandidateConfirmation(value.release_id, value.applicability_basis, value.order_basis,
                                 value.source_text, source)


def _rows(tx, name, *, lock=False, **filters):
    table = tx.repository.table(name)
    query = select(table).filter_by(**filters).order_by(*table.primary_key.columns)
    if lock:
        query = query.with_for_update()
    return [decode_record(name, dict(row)) for row in tx.session.execute(query).mappings()]


def _key(pair):
    return f"target_applicability.v1:{pair[0]}:{pair[1]}"


def _setting(tx, pair, *, lock=False):
    table = tx.repository.table("app_setting")
    query = select(table).where(table.c.setting_key == _key(pair).encode("utf-8"))
    if lock:
        query = query.with_for_update()
    row = tx.session.execute(query).mappings().one_or_none()
    return decode_record("app_setting", dict(row)) if row is not None else None


def _pair_reason(pair, models, families):
    groups = {r["group_id"]: r for r in models}
    model = groups.get(pair[0])
    family = next((r for r in families if r["family_id"] == pair[1]), None)
    if model is None or family is None:
        return "등록 모델 또는 계열이 없습니다. 모델·계열을 다시 확인해 주세요."
    owner = groups.get(family["group_id"])
    if owner is None or owner["category"] != model["category"]:
        return "모델과 계열 관리 모델의 분류가 다릅니다. 적용 관계를 확인해 주세요."
    if not model["analysis_enabled"] or model["category"] == "HPA":
        return "분석 제외 모델은 목표 N 후보를 제공하지 않습니다."
    for group in (model, owner):
        seen = set()
        while group is not None:
            if group["group_id"] in seen:
                return "모델의 원형·파생형 관계가 순환합니다. 관계를 확인해 주세요."
            seen.add(group["group_id"])
            parent_id = group["parent_group_id"]
            parent = groups.get(parent_id)
            if parent_id is not None and (parent is None or parent["category"] != group["category"]):
                return "모델의 원형·파생형 관계가 유효하지 않습니다. 관계를 확인해 주세요."
            group = parent
    return None


def _pair_fingerprint(pair, models, families):
    groups = {r["group_id"]: r for r in models}
    family = next(r for r in families if r["family_id"] == pair[1])
    ancestors = {}
    for start in (pair[0], family["group_id"]):
        current = start
        while current is not None and current not in ancestors:
            ancestors[current] = groups[current]
            current = groups[current]["parent_group_id"]
    return _fingerprint({"family": family, "models": sorted(ancestors.values(), key=lambda r: r["group_id"])})


def _fingerprint(value):
    def scalar(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"지원하지 않는 릴리즈 지문 자료형: {type(item).__name__}")
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False, default=scalar)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidates(pair, saved, releases, pair_fingerprint):
    missing = "해당 모델·계열에 적용 가능한 릴리즈를 확인한 근거가 없습니다."
    if saved is None:
        return (), (), False, missing
    payload = saved["setting_value"]
    if (not isinstance(payload, dict) or payload.get("schema") != 1
            or payload.get("model_id") != pair[0] or payload.get("family_id") != pair[1]
            or not isinstance(payload.get("candidates"), list)):
        return (), (), False, "저장된 모델 적용 근거의 형식이 잘못됐습니다. 다시 확인해 주세요."
    if payload.get("pair_fingerprint") != pair_fingerprint:
        return (), (), False, "모델 또는 계열 정보가 변경됐습니다. 적용 근거와 N을 다시 선택해 주세요."
    family_releases = {r["release_version_id"]: r for r in releases if r["family_id"] == pair[1]}
    orders = Counter(r["version_order"] for r in family_releases.values() if type(r["version_order"]) is int)
    available, excluded, seen = [], [], set()
    for saved_candidate in payload["candidates"]:
        try:
            require(isinstance(saved_candidate, dict), "확인 근거는 객체여야 합니다.")
            evidence = _confirmation(CandidateConfirmation(**deepcopy(saved_candidate["confirmation"])))
            require(evidence.release_id not in seen, "저장된 적용 릴리즈 ID가 중복됐습니다.")
            seen.add(evidence.release_id)
        except (TypeError, KeyError, StorageValidationError):
            # 훼손된 일부 근거를 무시해 정상 확인 목록으로 취급하지 않는다.
            return (), (), False, "저장된 모델 적용 근거가 불완전합니다. 전체 목록을 다시 확인해 주세요."
        release = family_releases.get(evidence.release_id)
        if release is None:
            reason = "릴리즈가 삭제·교체됐거나 다른 계열입니다. 적용 근거와 N을 다시 확인해 주세요."
        elif saved_candidate.get("fingerprint") != _fingerprint(release):
            reason = "릴리즈 정보가 변경됐습니다. 적용 표식·분기·순서와 N을 다시 확인해 주세요."
        elif type(release["version_order"]) is not int:
            reason = "릴리즈 순서가 미확인입니다. 순서를 확인한 뒤 N을 선택해 주세요."
        elif orders[release["version_order"]] > 1:
            reason = "같은 계열에 중복 순서가 있습니다. 순서를 확인한 뒤 N을 선택해 주세요."
        else:
            reason = None
        if reason:
            excluded.append(ExcludedCandidate(evidence.release_id, reason))
        else:
            available.append(TargetCandidate(ReleaseVersion(
                release["release_version_id"], release["family_id"], release["version_raw"],
                release["version_order"], release["released_at"]), evidence))
    available.sort(key=lambda c: (c.release.version_order, c.release_id))
    reason = None if available else (excluded[0].reason if excluded else "확인된 적용 목록에 선택 가능한 릴리즈가 없습니다.")
    return tuple(available), tuple(excluded), True, reason
