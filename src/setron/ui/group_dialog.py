"""버전(N) 기준 입력: 모델·계열별로 버전을 눌러 N을 선택하고 필요하면 나머지 단계를 직접 지정한다.

- '기본 후보'는 ``suggest_ladder``로 N 옆 칸을 채운다(사용자가 확인 후 저장).
- ``check_groups`` 결과를 막힘/경고로 보여 주고, 저장·이력·되돌리기는 저장 서비스(``GroupStore``)를 쓴다.
- 줄마다 ``tier_of`` 색 단계를 보여 준다. 그룹을 정하기 전(목표 1개)에도 같은 단계로 나온다.
- 저장한 그룹은 다음 '조회 적용'부터 계산에 쓴다. 이미 보이는 결과는 바꾸지 않는다.
"""

from PySide6.QtCore import QPoint, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget,
)

from setron.features.targets.groups import GroupHistoryPage
from setron.features.version.groups import GroupIssueCode
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, button, label
from setron.ui.group_data import (
    SLOT_LABELS, TIER_COLORS, TIER_LABELS, GroupSaveBlocked, MemoryGroupStore, Slot, VersionGroups, check_groups,
    group_store_for, release_tier, suggest_ladder,
)
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.target_data import target_pairs
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
        self.single = ({(t.model_id, t.family_id): t.release_id for t in targets} if targets is not None
                       else {p.key: p.current_id for p in self.pairs})
        self.drafts: dict[tuple[int, int], dict[int, Slot]] = {}  # 모델·계열 → 릴리즈 ID → 칸
        self.history_pages: dict[tuple[int, int], GroupHistoryPage] = {}
        self.history_request = ReadRequest(self)
        self.history_request.completed.connect(self._history_loaded)
        self.finished.connect(self._cancel_history)
        self._history_context = None
        self._history_reload: set[tuple[int, int]] = set()
        self.version_checks, self.slot_combos, self.tier_labels = {}, {}, {}
        self.slot_heading = None
        self.saved_any = False
        self.setWindowTitle("SETRON · 버전(N) 기준 입력")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(520, 540)
        self.setSizeGripEnabled(True)
        self.setStyleSheet(f"QDialog {{background:{CANVAS};}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(12)
        body.addWidget(label("버전(N) 기준 입력", 22, medium=True))
        self.pair_combo = ComboBox()
        self.pair_combo.setProperty("queryField", True)
        arrow = (ASSETS / "caret-down.svg").as_posix()
        self.pair_combo.setStyleSheet(
            "QComboBox {padding-right:28px;} QComboBox::drop-down {border:0;width:28px;}"
            f'QComboBox::down-arrow {{image:url("{arrow}");width:12px;height:12px;}}')
        self.pair_combo.setAccessibleName("기준을 정할 설비군·모델·계열")
        self.pair_combo.setFixedHeight(38)
        for pair in self.pairs:
            self.pair_combo.addItem(pair.label)
        if pair_key is None:
            saved_groups = getattr(source, "saved_groups", {})
            pair_key = next((pair.key for pair in self.pairs if pair.key in saved_groups
                             and (group_key is None or pair.group_key == group_key)), None)
        fallback = next((i for i, pair in enumerate(self.pairs) if pair.group_key == group_key), 0)
        self.pair_combo.setCurrentIndex(next((i for i, pair in enumerate(self.pairs) if pair.key == pair_key), fallback))
        self.pair_combo.setEnabled(bool(self.pairs))
        body.addWidget(self.pair_combo)

        tools = QHBoxLayout()
        self.suggest_button = button("기본 후보")
        self.suggest_button.setAutoDefault(False)
        self.suggest_button.clicked.connect(self.suggest)
        tools.addWidget(self.suggest_button)
        self.details_button = button("단계 직접 지정")
        self.details_button.setProperty("queryChoice", True)
        self.details_button.setAutoDefault(False)
        self.details_button.setCheckable(True)
        self.details_button.setToolTip("N-2, N-1, N+1, N+2 단계를 직접 지정하는 칸을 표시합니다.")
        self.details_button.toggled.connect(self._show_slot_choices)
        tools.addWidget(self.details_button)
        tools.addStretch(1)
        body.addLayout(tools)
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
        self.issues.setSpacing(4)
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
        self.close_button = button("닫기")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.accept)
        self.save_button = button("저장", primary=True)
        self.save_button.setAutoDefault(False)
        self.save_button.setToolTip("현재 선택한 설비 모델·계열의 N 기준을 저장합니다.")
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.close_button)
        actions.addWidget(self.save_button)
        body.addLayout(actions)
        self.pair_combo.currentIndexChanged.connect(self._pair_changed)
        self._pair_changed()
        width = min(760, parent.width() - 64) if parent is not None else 760
        height = min(780, parent.height() - 64) if parent is not None else 780
        self.resize(max(520, width), max(540, height))

    # --- 자료 ---
    @property
    def pair(self):
        index = self.pair_combo.currentIndex()
        return self.pairs[index] if index >= 0 else None

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
        self.suggest_button.setEnabled(ready)
        self.save_button.setEnabled(ready)
        if not ready:
            self.details_button.setEnabled(False)
            message = "설정할 모델·계열이 없습니다." if pair is None else "저장된 기준을 먼저 조회합니다."
            self.rows.addWidget(label(message, 13, color=SECONDARY), 0, 0)
            return
        self.rows.addWidget(label("버전 (최신 → 오래된 순)", 12, medium=True, color=SECONDARY), 0, 0)
        self.slot_heading = label("단계 지정", 12, medium=True, color=SECONDARY)
        self.rows.addWidget(self.slot_heading, 0, 1)
        self.rows.addWidget(label("색 단계", 12, medium=True, color=SECONDARY), 0, 2)
        options = [o for o in pair.options if o.release.family_id == pair.family_id]
        ordered = sorted(options, key=lambda o: (o.release.version_order is None, -(o.release.version_order or 0)))
        draft = self.draft(pair)
        for row, option in enumerate(ordered, 1):
            release = option.release
            release_id = release.release_version_id
            name = release.version_raw + (f" · {option.unavailable_reason}" if option.unavailable_reason else "")
            choice = VersionChoice(name)
            choice.setAccessibleName(f"{name} · 기준 N")
            choice.setStyleSheet(f"QCheckBox {{background:#f9fafb;border:1px solid {LINE};border-radius:8px;padding-left:8px;}}"
                                 f"QCheckBox:hover {{border-color:{PRIMARY};}}"
                                 f"QCheckBox:checked {{background:#edf0fd;border-color:{PRIMARY};}}"
                                 f"QCheckBox:focus {{border:2px solid {PRIMARY};}}")
            choice.setChecked(draft.get(release_id) is Slot.N)
            choice.toggled.connect(lambda checked, rid=release_id: self.set_slot(rid, Slot.N if checked else None))
            self.rows.addWidget(choice, row, 0)
            self.version_checks[release_id] = choice
            combo = ComboBox()
            combo.setProperty("queryField", True)
            combo.setFixedSize(96, 32)
            combo.setAccessibleName(f"{release.version_raw} 단계 지정")
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
        for issue in found:
            ink = BLOCK_INK if issue.blocks or issue.code is GroupIssueCode.ORDER_CROSSED else WARN_INK
            line = label(("막힘 · " if issue.blocks else "경고 · ") + issue.message, 12, color=ink, wrap=True)
            self.issues.addWidget(line)
        self.save_button.setEnabled(family is not None and pair.key not in self._history_reload
                                    and not any(issue.blocks for issue in found))
        self.suggest_button.setEnabled(family is not None and groups.has_target)
        self.suggest_button.setToolTip(
            "선택한 N 버전은 유지하고 앞뒤 단계를 기본 후보로 다시 채웁니다."
            if len(groups.members(Slot.N)) > 1 else "선택한 N 기준의 앞뒤 버전으로 비어 있는 단계를 채웁니다."
        )

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

    @staticmethod
    def _set_history_row_active(row, active):
        frame, caption, revert = row
        frame.setProperty("activeHistory", active)
        style = (f"background:{ACCENT_SOFT};border:1px solid {PRIMARY};border-radius:8px;" if active
                 else f"border:1px solid transparent;border-bottom-color:{LINE};")
        frame.setStyleSheet(f"QFrame#groupHistoryRow {{{style}}}")
        caption.setAccessibleName(("현재 기준 · " if active else "") + caption.text())
        revert.setEnabled(not active)
        revert.setToolTip("현재 적용 중인 기준입니다." if active else "")

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
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 6, 10, 6)
        caption = label(f"{entry.saved_at:%Y-%m-%d %H:%M}\n{self._history_versions(entry.groups)}", 12,
                        color=SECONDARY, wrap=True)
        caption.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        row.addWidget(caption, 1)
        revert = button("되돌리기")
        revert.setAutoDefault(False)
        revert.setFixedHeight(30)
        revert.setAccessibleName(f"{entry.saved_at:%Y-%m-%d %H:%M} 기록 {entry.sequence} 기준으로 되돌리기")
        revert.clicked.connect(lambda checked=False, value=entry: self.revert(value))
        row.addWidget(revert, 0, Qt.AlignmentFlag.AlignTop)
        self.history_rows[entry.sequence] = (frame, caption, revert)
        self._set_history_row_active(self.history_rows[entry.sequence], active is not None and entry.sequence == active.sequence)
        self.history.insertWidget(0 if prepend else self.history.count(), frame)
        return revert if revert.isEnabled() else caption

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
    def set_slot(self, release_id, slot):
        draft = self.draft()
        if slot is None:
            draft.pop(release_id, None)
        else:
            draft[release_id] = slot
        choice, combo = self.version_checks[release_id], self.slot_combos[release_id]
        with QSignalBlocker(choice), QSignalBlocker(combo):
            choice.setChecked(slot is Slot.N)
            combo.setCurrentIndex(SLOT_CHOICES.index(slot))
        self.status.clear()
        self._refresh()

    def suggest(self):
        family = self.family()
        if family is None or self.pair.key not in self.history_pages:
            return False
        groups = self.groups()
        if not groups.has_target:
            self.status.setText("N 칸을 먼저 고르면 옆 칸을 채울 수 있습니다.")
            return False
        filled = suggest_ladder(family, groups, replace_surrounding=len(groups.members(Slot.N)) > 1)
        self.drafts[self.pair.key] = {rid: slot for slot, ids in filled.slots.items() for rid in ids}
        self.status.clear()
        self._render()
        return True

    def _store_change(self, write, *, restoring=False):
        pair, family = self.pair, self.family()
        if pair is None or pair.key not in self.history_pages or pair.key in self._history_reload:
            return False
        try:
            result = write(pair.key, family)
        except GroupSaveBlocked as error:
            self.status.setText(f"{'되돌리지 않았습니다' if restoring else '저장하지 않았습니다'}: {error}")
            return False
        except Exception as error:  # DB 연결·형식 오류는 그대로 안내하고 초안은 유지한다.
            self.status.setText(f"{'되돌리지 못했습니다' if restoring else '저장하지 못했습니다'}: {error}")
            return False
        self.drafts[pair.key] = {rid: slot for slot, ids in result.saved.groups.slots.items() for rid in ids}
        page = self.history_pages[pair.key]
        refresh_history = result.changed or page.current != result.saved
        scrollbar = self.scroll.verticalScrollBar()
        position = scrollbar.value()
        if refresh_history:
            self._cancel_history()
            if restoring:
                self.history_pages[pair.key] = GroupHistoryPage(result.saved, page.entries, page.next_cursor)
                if self._extra_history_sequence is not None and self._extra_history_sequence != result.saved.sequence:
                    position = self._remove_extra_history_row(position)
                self._show_extra_history_row(result.saved)
                for sequence, row in self.history_rows.items():
                    self._set_history_row_active(row, sequence == result.saved.sequence)
                self._show_history_status()
            else:
                self._history_reload.add(pair.key)
                entries = (result.saved, *page.entries) if result.changed else page.entries
                self.history_pages[pair.key] = GroupHistoryPage(result.saved, entries[:HISTORY_PAGE_SIZE], None)
                self._render_history()
        self._render()
        if restoring:
            scrollbar.setValue(position)
        elif refresh_history:
            self._request_history(None)
        if not result.changed:
            self.status.setText("이미 적용 중인 기준입니다." if restoring else "지금 기준과 같아 저장하지 않았습니다.")
            return True
        self.saved_any = True
        warned = f" · 경고 {len(result.warnings)}건" if result.warnings else ""
        action = "기준을 되돌렸습니다" if restoring else "저장했습니다"
        self.status.setText(f"{action}{warned}. '조회 적용'을 누르면 새 기준으로 계산합니다.")
        return True

    def save(self):
        if self.pair is None or self.pair.key not in self.history_pages:
            return False
        groups = self.groups()
        return self._store_change(lambda key, family: self.store.save(key, groups, family))

    def revert(self, entry):
        page = self.history_pages.get(self.pair.key) if self.pair is not None else None
        if page is None or (entry != page.current and entry not in page.entries):
            return False
        return self._store_change(lambda key, family: self.store.restore(key, entry.sequence, family=family), restoring=True)
