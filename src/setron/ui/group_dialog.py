"""기준 버전 설정: 모델·계열별로 버전을 눌러 N을 선택하고 필요하면 나머지 단계를 직접 지정한다.

- 기본 화면에서는 기준 N을 고르고, 수동 설정을 켜면 버전별 단계를 직접 지정한다.
- ``check_groups`` 결과를 막힘/경고로 보여 주고, 저장·이력·되돌리기는 저장 서비스(``GroupStore``)를 쓴다.
- 줄마다 ``tier_of`` 색 단계를 보여 준다. 그룹을 정하기 전(목표 1개)에도 같은 단계로 나온다.
- 편집하거나 이력을 불러온 기준은 저장에 성공하면 창을 닫아 적용한다.
"""

from collections import Counter
from html import escape

from PySide6.QtCore import QPoint, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from setron.features.targets.groups import GroupHistoryPage
from setron.features.version.groups import GroupIssueCode
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, button, label
from setron.ui.dashboard_data import family_name, model_name
from setron.ui.group_data import (
    SLOT_LABELS, TIER_COLORS, TIER_LABELS, GroupSaveBlocked, MemoryGroupStore, Slot, VersionGroups, check_groups,
    group_store_for, release_tier,
)
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.target_data import MODEL_APPLICABILITY_UNCONFIRMED, target_pairs
from setron.ui.theme import ACCENT_SOFT, ASSETS, CANVAS, LINE, PRIMARY, SECONDARY
from setron.ui.version_filter_dialog import VersionChoice

SLOT_CHOICES = (None, Slot.N_MINUS_2, Slot.N_MINUS_1, Slot.N, Slot.N_PLUS_1, Slot.N_PLUS_2)
BLOCK_INK, WARN_INK = "#ad344d", "#8a6400"
HISTORY_PAGE_SIZE = 10


class VersionGroupDialog(QDialog):
    def __init__(self, source, *, targets=None, group_key=None, pair_key=None, store=None, parent=None):
        super().__init__(parent)
        self.source = source
        self.store = store or group_store_for(source)
        self.pairs = target_pairs(source)
        self._pairs_by_model = {}
        for pair in self.pairs:
            self._pairs_by_model.setdefault(pair.model_id, {})[pair.family_id] = pair
        self.single = ({(t.model_id, t.family_id): t.release_id for t in targets} if targets is not None
                       else {p.key: p.current_id for p in self.pairs})
        self.drafts: dict[tuple[int, int], dict[int, Slot]] = {}  # 모델·계열 → 릴리즈 ID → 칸
        self.restore_drafts = {}  # 모델·계열 → 불러온 이력
        self.history_pages: dict[tuple[int, int], GroupHistoryPage] = {}
        self.history_request = ReadRequest(self)
        self.history_request.completed.connect(self._history_loaded)
        self.finished.connect(self._cancel_history)
        self._history_context = None
        self._history_reload: set[tuple[int, int]] = set()
        self.version_checks, self.slot_combos, self.tier_labels = {}, {}, {}
        self.slot_heading = None
        self.saved_any = False
        self.setWindowTitle("SETRON · 기준 버전 설정")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(520, 540)
        self.setSizeGripEnabled(True)
        self.setStyleSheet(f"QDialog {{background:{CANVAS};}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(12)
        body.addWidget(label("기준 버전 설정", 22, medium=True))
        self.pair_combo = ComboBox()
        self.family_combo = ComboBox()
        arrow = (ASSETS / "caret-down.svg").as_posix()
        for combo in (self.pair_combo, self.family_combo):
            combo.setProperty("queryField", True)
            combo.setStyleSheet(
                "QComboBox {padding-right:28px;} QComboBox::drop-down {border:0;width:28px;}"
                f'QComboBox::down-arrow {{image:url("{arrow}");width:12px;height:12px;}}')
            combo.setMinimumContentsLength(8)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setFixedHeight(38)
        self.pair_combo.setAccessibleName("기준을 정할 모델")
        self.family_combo.setAccessibleName("기준을 정할 계열")
        names = {model_id: model_name(source, model_id) for model_id in self._pairs_by_model}
        name_counts = Counter(names.values())
        canonical_names = getattr(source, "model_names", None) or {}
        for model_id, name in names.items():
            if model_id not in canonical_names and name_counts[name] > 1:
                name = f"모델 {model_id}"
            self.pair_combo.addItem(name, model_id)
        if pair_key is None:
            saved_groups = getattr(source, "saved_groups", {})
            pair_key = next((pair.key for pair in self.pairs if pair.key in saved_groups
                             and (group_key is None or pair.group_key == group_key)), None)
        fallback = next((pair for pair in self.pairs if pair.group_key == group_key), next(iter(self.pairs), None))
        selected = next((pair for pair in self.pairs if pair.key == pair_key), fallback)
        if selected is not None:
            self.pair_combo.setCurrentIndex(self.pair_combo.findData(selected.model_id))
        self._set_family_choices(selected.family_id if selected is not None else None)
        self.pair_combo.setEnabled(bool(self.pairs))
        selection = QHBoxLayout()
        selection.addWidget(self.family_combo, 1)
        selection.addWidget(self.pair_combo, 1)

        self.details_button = button("수동 설정")
        self.details_button.setObjectName("versionGroupManual")
        self.details_button.setProperty("queryChoice", True)
        self.details_button.setAutoDefault(False)
        self.details_button.setCheckable(True)
        self.details_button.setFixedHeight(38)
        self.details_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.details_button.setToolTip("버전별 단계를 직접 지정하는 칸을 펼치거나 접습니다.")
        self.details_button.setStyleSheet(
            "QPushButton#versionGroupManual {padding:0 12px;text-align:center;min-height:36px;max-height:36px;}"
            f"QPushButton#versionGroupManual:focus {{border:1px solid {PRIMARY};}}")
        self.details_button.toggled.connect(self._show_slot_choices)
        selection.addWidget(self.details_button)
        body.addLayout(selection)
        QWidget.setTabOrder(self.family_combo, self.pair_combo)
        QWidget.setTabOrder(self.pair_combo, self.details_button)
        self.legend = QHBoxLayout()
        self.legend.setSpacing(6)
        self.legend.addStretch(1)
        for tier, (background, ink) in TIER_COLORS.items():
            self.legend.addWidget(self._chip(TIER_LABELS[tier], background, ink))
        body.addLayout(self.legend)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(140)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("canvas")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.rows_card = Card(padding=12)
        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(12)
        self.rows.setVerticalSpacing(6)
        self.rows.setColumnStretch(0, 1)
        self.rows_card.body.addLayout(self.rows)
        layout.addWidget(self.rows_card)
        self.issues = QVBoxLayout()
        self.issues.setSpacing(8)
        layout.addLayout(self.issues)
        self.history_card = Card(padding=12)
        self.history_card.body.setSpacing(6)
        self.history_card.body.addWidget(label("저장 이력", 14, medium=True))
        self.history = QVBoxLayout()
        self.history.setSpacing(6)
        self.history_rows = {}
        self._extra_history_sequence = None
        self.history_card.body.addLayout(self.history)
        self.history_status = label("", 12, color=SECONDARY, wrap=True)
        self.history_card.body.addWidget(self.history_status)
        self.history_more = button("더 보기")
        self.history_more.setAutoDefault(False)
        self.history_more.clicked.connect(self._load_more_history)
        self.history_card.body.addWidget(self.history_more, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.history_card)
        layout.addStretch()
        self.scroll.setWidget(content)
        body.addWidget(self.scroll, 1)

        self.status = label("", 13, color=SECONDARY, wrap=True)
        body.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = button("취소")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = button("저장", primary=True)
        self.save_button.setAutoDefault(False)
        self.save_button.setToolTip("현재 모델·계열의 N 기준을 저장하고 창을 닫습니다.")
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.save_button)
        body.addLayout(actions)
        self.pair_combo.currentIndexChanged.connect(self._model_changed)
        self.family_combo.currentIndexChanged.connect(self._pair_changed)
        self._pair_changed()
        width = min(760, parent.width() - 64) if parent is not None else 760
        height = min(780, parent.height() - 64) if parent is not None else 780
        self.resize(max(520, width), max(540, height))

    # --- 자료 ---
    @property
    def pair(self):
        choices = self._pairs_by_model.get(self.pair_combo.currentData(), {})
        return choices.get(self.family_combo.currentData())

    def _set_family_choices(self, selected_id=None):
        choices = self._pairs_by_model.get(self.pair_combo.currentData(), {})
        with QSignalBlocker(self.family_combo):
            self.family_combo.clear()
            for family_id in choices:
                self.family_combo.addItem(f"계열 {family_name(self.source, family_id)}", family_id)
            self.family_combo.setCurrentIndex(max(0, self.family_combo.findData(selected_id)))
        self.family_combo.setVisible(len(choices) > 1)

    def _model_changed(self, *_):
        self._set_family_choices()
        self._pair_changed()

    def family(self, pair=None):
        pair = pair or self.pair
        return self.source.families.get(pair.family_id) if pair else None

    def draft(self, pair=None) -> dict[int, Slot]:
        pair = pair or self.pair
        if pair.key not in self.drafts:
            saved = self.history_pages[pair.key].current
            if saved is not None:
                self.drafts[pair.key] = {rid: slot for slot, ids in saved.groups.slots.items() for rid in ids}
            else:
                target = self.single.get(pair.key)
                self.drafts[pair.key] = {target: Slot.N} if target is not None else {}
        return self.drafts[pair.key]

    def groups(self, pair=None) -> VersionGroups:
        slots: dict[Slot, set[int]] = {}
        for rid, slot in self.draft(pair).items():
            slots.setdefault(slot, set()).add(rid)
        return VersionGroups({slot: frozenset(ids) for slot, ids in slots.items()})

    def _uses_single(self, pair):
        """아직 그룹을 저장하지 않았고 목표 1개 그대로면 기존 목표 1개 비교로 색을 낸다."""
        target = self.single.get(pair.key)
        return (self.history_pages[pair.key].current is None
                and self.draft(pair) == ({target: Slot.N} if target is not None else {}))

    # --- 화면 ---
    @staticmethod
    def _chip(text, background, ink):
        chip = label(text, 11, medium=True, color=ink)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setMinimumWidth(64)
        chip.setFixedHeight(22)
        chip.setStyleSheet(f"background:{background};color:{ink};border-radius:4px;padding:0 6px;")
        return chip

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                self._clear(item.layout())

    def _pair_changed(self, *_):
        self._cancel_history()
        self.status.clear()
        self._render()
        self._render_history()
        self.scroll.verticalScrollBar().setValue(0)
        if self.pair is not None and (self.pair.key not in self.history_pages or self.pair.key in self._history_reload):
            self._request_history(None)

    def _show_slot_choices(self, checked):
        self.details_button.setAccessibleName("수동 설정 접기" if checked else "수동 설정 펼치기")
        if self.slot_heading is not None:
            self.slot_heading.setVisible(checked)
        for combo in self.slot_combos.values():
            combo.setVisible(checked)

    def _render(self, *_):
        self._clear(self.rows)
        self.version_checks.clear()
        self.slot_combos.clear()
        self.tier_labels.clear()
        self.slot_heading = None
        pair = self.pair
        ready = pair is not None and pair.key in self.history_pages
        self.save_button.setEnabled(ready)
        if not ready:
            self.details_button.setEnabled(False)
            self._show_slot_choices(False)
            message = "설정할 모델·계열이 없습니다." if pair is None else "저장된 기준을 먼저 조회합니다."
            self.rows.addWidget(label(message, 13, color=SECONDARY), 0, 0)
            return
        self.rows.addWidget(label("버전 (최신 → 오래된 순)", 12, medium=True, color=SECONDARY), 0, 0)
        self.slot_heading = label("수동 설정", 12, medium=True, color=SECONDARY)
        self.rows.addWidget(self.slot_heading, 0, 1)
        self.rows.addWidget(label("기준", 12, medium=True, color=SECONDARY), 0, 2)
        options = [o for o in pair.options if o.release.family_id == pair.family_id]
        ordered = sorted(options, key=lambda o: (o.release.version_order is None, -(o.release.version_order or 0)))
        draft = self.draft(pair)
        for row, option in enumerate(ordered, 1):
            release = option.release
            release_id = release.release_version_id
            reason = option.unavailable_reason
            name = (f"{release.version_raw} · {reason}"
                    if reason and reason != MODEL_APPLICABILITY_UNCONFIRMED else release.version_raw)
            choice = VersionChoice(name)
            choice.setAccessibleName(f"{name} · 기준 N")
            choice.setStyleSheet(f"QCheckBox {{background:#f9fafb;border:1px solid {LINE};border-radius:8px;padding-left:8px;}}"
                                 f"QCheckBox:hover {{border-color:{PRIMARY};}}"
                                 f"QCheckBox:checked {{background:#edf0fd;border-color:{PRIMARY};}}"
                                 f"QCheckBox:focus {{border:2px solid {PRIMARY};}}")
            choice.setChecked(draft.get(release_id) is Slot.N)
            choice.toggled.connect(lambda checked, rid=release_id: self.set_slot(
                rid, Slot.N if checked else None, fill_range=checked))
            self.rows.addWidget(choice, row, 0)
            self.version_checks[release_id] = choice
            combo = ComboBox()
            combo.setProperty("queryField", True)
            combo.setFixedSize(96, 32)
            combo.setAccessibleName(f"{release.version_raw} 수동 설정")
            for slot in SLOT_CHOICES:
                combo.addItem("—" if slot is None else SLOT_LABELS[slot], slot)
            combo.setCurrentIndex(SLOT_CHOICES.index(draft.get(release_id)))
            combo.currentIndexChanged.connect(
                lambda index, rid=release_id: self.set_slot(rid, SLOT_CHOICES[index]))
            self.rows.addWidget(combo, row, 1)
            self.slot_combos[release_id] = combo
            tier = self.tier_labels[release_id] = self._chip("", "#eceef1", SECONDARY)
            self.rows.addWidget(tier, row, 2)
        if not ordered:
            self.rows.addWidget(label("이 계열에 등록된 릴리즈가 없습니다.", 13, color=SECONDARY), 1, 0, 1, 3)
        self.details_button.setEnabled(bool(ordered))
        self._show_slot_choices(bool(ordered) and self.details_button.isChecked())
        self._refresh()

    def _refresh(self):
        """지금 초안의 선택 수·색 단계·확인 결과를 갱신한다."""
        pair, family = self.pair, self.family()
        groups = self.groups(pair)
        single = self._uses_single(pair)
        for rid, chip in self.tier_labels.items():
            tier = release_tier(family, rid, None if single else groups, self.single.get(pair.key))
            background, ink = TIER_COLORS[tier]
            chip.setText(TIER_LABELS[tier])
            chip.setStyleSheet(f"background:{background};color:{ink};border-radius:4px;padding:0 6px;")
        self._clear(self.issues)
        found = check_groups(family, groups) if family is not None else ()
        blocked = any(issue.blocks for issue in found)
        warning_title = "경고" if blocked else "경고 · 저장 가능"
        for issue in found:
            red = issue.blocks or issue.code is GroupIssueCode.ORDER_CROSSED
            ink, background = (BLOCK_INK, "#fcf0f2") if red else (WARN_INK, "#fff6d2")
            notice = Card(padding=9)
            notice.setObjectName("groupIssue")
            notice.setStyleSheet(f"QFrame#groupIssue {{background:{background};border-radius:8px;}}")
            notice.body.setSpacing(3)
            notice.body.addWidget(label("저장 불가" if issue.blocks else warning_title, 13, medium=True, color=ink))
            message = label(issue.message, 13, color=ink, wrap=True)
            message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            notice.body.addWidget(message)
            self.issues.addWidget(notice)
        self.save_button.setEnabled(family is not None and pair.key not in self._history_reload
                                    and not blocked)

    def _render_history(self):
        self._clear(self.history)
        self.history_rows.clear()
        self._extra_history_sequence = None
        page = self.history_pages.get(self.pair.key) if self.pair is not None else None
        if page is not None and page.entries:
            for entry in page.entries:
                self._add_history_row(entry, page.current)
            self._show_extra_history_row(page.current)
        elif page is not None:
            self.history.addWidget(label("저장 이력이 없습니다.", 12, color=SECONDARY))
        self._show_history_status()

    def _show_extra_history_row(self, active):
        if active is not None and active.sequence not in self.history_rows:
            self._add_history_row(active, active, prepend=True)
            self._extra_history_sequence = active.sequence

    def _remove_extra_history_row(self, position):
        frame, _, _ = self.history_rows.pop(self._extra_history_sequence)
        self._extra_history_sequence = None
        top = frame.mapTo(self.scroll.widget(), QPoint()).y()
        height = frame.height() + self.history.spacing()
        self.history.removeWidget(frame)
        frame.hide()
        frame.deleteLater()
        return position - min(height, max(0, position - top))

    def _set_history_row_active(self, entry, active):
        frame, caption, revert = self.history_rows[entry.sequence]
        frame.setProperty("activeHistory", active)
        style = (f"background:{ACCENT_SOFT};border:1px solid {PRIMARY};border-radius:8px;" if active
                 else f"border:1px solid transparent;border-bottom-color:{LINE};")
        frame.setStyleSheet(f"QFrame#groupHistoryRow {{{style}}}")
        prefix = "현재 기준 · " if active else ""
        caption.setAccessibleName(f"{prefix}{entry.saved_at:%Y-%m-%d %H:%M}  {caption.text()}")
        if active and revert is not None:
            if revert.hasFocus():
                caption.setFocus(Qt.FocusReason.OtherFocusReason)
            frame.layout().removeWidget(revert)
            revert.hide()
            revert.deleteLater()
            revert = None
        elif not active and revert is None:
            revert = button("되돌리기")
            revert.setAutoDefault(False)
            revert.setFixedHeight(30)
            revert.setAccessibleName(f"{entry.saved_at:%Y-%m-%d %H:%M} 기록 {entry.sequence} 기준으로 되돌리기")
            revert.clicked.connect(lambda checked=False, value=entry: self.revert(value))
            frame.layout().addWidget(revert, 0, Qt.AlignmentFlag.AlignVCenter)
        self.history_rows[entry.sequence] = frame, caption, revert

    def _history_versions(self, groups):
        family = self.family()
        members = groups.members(Slot.N)
        releases = [family.get(member) for member in sorted(members)] if family is not None else []
        known = sorted((release for release in releases if release is not None),
                       key=lambda release: (release.version_order is None, -(release.version_order or 0)))
        names = [release.version_raw for release in known]
        names.extend(["릴리즈 미확인"] * (len(members) - len(known)))
        return ", ".join(names) or "미지정"

    def _add_history_row(self, entry, active, *, prepend=False):
        frame = QFrame()
        frame.setObjectName("groupHistoryRow")
        frame.setMinimumHeight(44)
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(16)
        date_text = label(f"{entry.saved_at:%Y-%m-%d %H:%M}", 13, color=SECONDARY, numeric=True)
        date_text.setObjectName("groupHistoryDate")
        date_text.setFixedWidth(124)
        row.addWidget(date_text)
        versions = self._history_versions(entry.groups).splitlines()
        caption = label(" ".join(versions), 13, color=SECONDARY)
        caption.setToolTip("<qt>" + "<br>".join(map(escape, versions)) + "</qt>")
        caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        caption.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        row.addWidget(caption, 1)
        self.history_rows[entry.sequence] = frame, caption, None
        self._set_history_row_active(entry, active is not None and entry.sequence == active.sequence)
        self.history.insertWidget(0 if prepend else self.history.count(), frame)
        return self.history_rows[entry.sequence][2] or caption

    def _show_history_status(self, message=""):
        self.history_status.setText(message)
        self.history_status.setVisible(bool(message))
        key = self.pair.key if self.pair is not None else None
        page = self.history_pages.get(key)
        reload_needed = key in self._history_reload
        self.history_more.setText("다시 조회" if reload_needed else "더 보기")
        self.history_more.setVisible(reload_needed or (page is not None and page.next_cursor is not None))

    def _cancel_history(self, *_):
        self.history_request.cancel()
        self._history_context = None

    def _load_more_history(self):
        if self.pair is None or self.history_request.busy:
            return
        if self.pair.key in self._history_reload:
            self._request_history(None)
            return
        page = self.history_pages.get(self.pair.key)
        if page is not None and page.next_cursor is not None:
            self._request_history(page.next_cursor)

    def _request_history(self, cursor):
        key, store = self.pair.key, self.store
        self._history_context = (key, cursor)
        self._show_history_status("저장 이력을 불러오는 중입니다…")
        loader = lambda: store.history_page(key, cursor=cursor, limit=HISTORY_PAGE_SIZE)
        self.history_request.start(loader if isinstance(store, MemoryGroupStore) else BackgroundRead(loader))

    def _history_loaded(self, result, error):
        context, self._history_context = self._history_context, None
        if context is None or self.pair is None or self.pair.key != context[0]:
            return
        key, cursor = context
        page = self.history_pages.get(key)
        if error is not None or not isinstance(result, GroupHistoryPage):
            if cursor is None:
                self._history_reload.add(key)
                self.save_button.setEnabled(False)
            self._show_history_status("저장 이력을 읽지 못했습니다. 다시 조회해 주세요.")
            return
        if cursor is None:
            self._history_reload.discard(key)
            self.history_pages[key] = result
            self._render()
            self._render_history()
            if page is not None and page.current != result.current:
                self._show_history_status("저장된 기준이 변경되었습니다. 현재 선택을 확인한 뒤 저장해 주세요.")
            return
        if result.current != page.current:
            self._history_reload.add(key)
            self.save_button.setEnabled(False)
            self._show_history_status("저장된 기준이 변경되었습니다. 최신 이력을 다시 조회해 주세요.")
            return
        had_focus = self.history_more.hasFocus()
        scrollbar = self.scroll.verticalScrollBar()
        position = scrollbar.value()
        if any(entry.sequence == self._extra_history_sequence for entry in result.entries):
            position = self._remove_extra_history_row(position)
        self.history_pages[key] = GroupHistoryPage(result.current, page.entries + result.entries, result.next_cursor)
        first_added = None
        for entry in result.entries:
            focus_target = self._add_history_row(entry, result.current)
            if first_added is None:
                first_added = focus_target
        if had_focus and first_added is not None:
            # 추가한 행이 레이아웃에 표시된 뒤 초점을 옮긴다.
            generation = self.history_request.generation
            QTimer.singleShot(0, self, lambda: self._finish_history_append(generation, first_added, position))
        else:
            self._show_history_status()
            scrollbar.setValue(position)

    def _finish_history_append(self, generation, focus_target, position):
        if self.history_request.generation != generation:
            return
        scrollbar = self.scroll.verticalScrollBar()
        if self.history_more.hasFocus():
            focus_target.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            position = scrollbar.value()
        self._show_history_status()
        scrollbar.setValue(position)

    # --- 동작 ---
    def set_slot(self, release_id, slot, *, fill_range=False):
        draft = self.draft()
        release_ids = (release_id,)
        if fill_range:
            ordered = list(self.version_checks)
            selected = [index for index, rid in enumerate(ordered)
                        if rid == release_id or draft.get(rid) is Slot.N]
            release_ids = ordered[selected[0]:selected[-1] + 1]
        for rid in release_ids:
            if slot is None:
                draft.pop(rid, None)
            else:
                draft[rid] = slot
            choice, combo = self.version_checks[rid], self.slot_combos[rid]
            with QSignalBlocker(choice), QSignalBlocker(combo):
                choice.setChecked(slot is Slot.N)
                combo.setCurrentIndex(SLOT_CHOICES.index(slot))
        self.status.clear()
        self._refresh()

    def save(self):
        pair, family = self.pair, self.family()
        if pair is None or pair.key not in self.history_pages or pair.key in self._history_reload:
            return False
        groups = self.groups(pair)
        entry = self.restore_drafts.get(pair.key)
        try:
            if entry is not None and entry.groups == groups:
                result = self.store.restore(pair.key, entry.sequence, family=family)
            else:
                result = self.store.save(pair.key, groups, family)
        except GroupSaveBlocked as error:
            self.status.setText(f"저장하지 않았습니다. {error}")
            return False
        except Exception as error:  # DB 연결·형식 오류는 그대로 안내하고 초안은 유지한다.
            self.status.setText(f"저장하지 못했습니다. {error}")
            return False
        self.saved_any = result.changed
        self.accept()
        return True

    def revert(self, entry):
        pair = self.pair
        page = self.history_pages.get(pair.key) if pair is not None else None
        if page is None or (entry != page.current and entry not in page.entries):
            return False
        self.restore_drafts[pair.key] = entry
        self.drafts[pair.key] = {rid: slot for slot, ids in entry.groups.slots.items() for rid in ids}
        self._render()
        self.status.setText("이력의 기준을 불러왔습니다. 저장하면 적용합니다.")
        return True
