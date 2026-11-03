"""공유 노트의 현재 유효한 Item별 O를 모델·버전의 N 후보 근거로 모은다."""

from collections import defaultdict

from sqlalchemy import select

from setron.features.item.schemas import ItemState
from setron.features.release.shared_models import SharedNoteItemService, canonical_column_model
from setron.features.storage.inputs import merge_sources
from setron.features.storage.validation import decode_record

from .schemas import CandidateConfirmation, ExcludedCandidate


class SharedNoteCandidates:
    """한 조회 트랜잭션에서 표식을 읽고 여러 모델이 같은 Item 검증 결과를 공유한다.

    저장된 구분란이 있는 모델·버전은 그 표식을 우선한다. 마지막 O가 없어졌거나
    근거가 무효라면 이전 수동 후보 때문에 다시 선택 가능해지지 않도록 한다.
    """

    def __init__(self, tx, releases, models, families):
        self.tx = tx
        self.service = SharedNoteItemService(None)
        self.releases = {row["release_version_id"]: row for row in releases}
        self.models = {row["group_id"]: row for row in models}
        self.families = {row["family_id"]: row for row in families}
        self.settings = defaultdict(list)
        self.loaded = {}
        prefix = "shared_note_item_models.v1:"
        table = tx.repository.table("app_setting")
        # VARBINARY 접두사 범위로 한정해 다른 관리 설정은 읽거나 해석하지 않는다.
        query = select(table).where(table.c.setting_key >= prefix.encode("utf-8"),
                                    table.c.setting_key < b"shared_note_item_models.v1;")
        for saved in tx.session.execute(query).mappings():
            row = decode_record("app_setting", dict(saved))
            key = row["setting_key"]
            if not key.startswith(prefix):
                continue
            parts = key[len(prefix):].split(":")
            if len(parts) != 2 or not all(part.isascii() and part.isdecimal() for part in parts):
                continue
            release_id, item_id = map(int, parts)
            if release_id in self.releases:
                self.settings[release_id].append((item_id, row["setting_value"]))

    def for_pair(self, pair):
        """확인 근거, 제외 사유, 수동 후보보다 우선할 릴리즈 ID 집합을 반환한다."""
        model_id, family_id = pair
        confirmed, excluded, covered = [], [], set()
        family = self.families[family_id]
        owner = self.models[family["group_id"]]
        for release_id, settings in self.settings.items():
            release = self.releases[release_id]
            if release["family_id"] != family_id:
                continue
            positives, reasons = [], []
            for item_id, payload in settings:
                entries = payload.get("entries") if isinstance(payload, dict) else None
                malformed = not isinstance(entries, list) or not entries or any(
                    not isinstance(entry, dict) or type(entry.get("model_id")) is not int
                    or entry["model_id"] <= 0 or not isinstance(entry.get("mark"), dict)
                    for entry in entries)
                if not malformed and not any(entry.get("model_id") == model_id for entry in entries):
                    continue  # 해당 모델의 구분란이 없다는 사실을 X로 추정하지 않는다.
                covered.add(release_id)
                key = release_id, item_id
                if key not in self.loaded:
                    try:
                        self.loaded[key] = self.service.load_in(self.tx, release_id, item_id)
                    except (ValueError, TypeError, KeyError):
                        self.loaded[key] = None
                marks = self.loaded[key]
                if marks is None:
                    reasons.append("저장된 공유 노트의 모델 적용 표식을 다시 확인해 주세요.")
                    continue
                for value in marks:
                    if value.model_id != model_id:
                        continue
                    if canonical_column_model(value.mark.note_model, owner["group_name"]) is None:
                        reasons.append("공유 노트와 현재 계열 관리 모델의 연결을 다시 확인해 주세요.")
                    elif value.state is ItemState.APPLIED and value.reason is None:
                        positives.append((item_id, value))
                    elif value.reason:
                        reasons.append(value.reason)
            if positives:
                sources = tuple({"model_applicability": {
                    "sheet": value.mark.sheet_name,
                    "cells": [value.mark.header_address, value.mark.mark_address],
                }} for _, value in positives)
                order_source = (release.get("source_reference") or {}).get("version_order")
                if order_source is not None:
                    sources += ({"version_order": order_source},)
                confirmed.append(CandidateConfirmation(
                    release_id,
                    "해당 모델의 현재 유효한 O 항목이 하나 이상인 공유 릴리즈",
                    "같은 계열의 확인된 version_order를 사용하며 미확인·중복 순서는 제외",
                    source_text="공유 노트 O 항목 ID: " + ", ".join(str(item_id) for item_id, _ in positives),
                    source_reference=merge_sources(*sources),
                ))
            elif release_id in covered:
                reason = reasons[0] if reasons else "해당 모델에 O인 항목이 없습니다. 확인된 다른 목표 N을 선택해 주세요."
                excluded.append(ExcludedCandidate(release_id, reason))
        return tuple(confirmed), tuple(excluded), covered
