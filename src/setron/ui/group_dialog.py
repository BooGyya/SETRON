"""버전(N) 기준 입력: 모델·계열별로 버전을 눌러 N을 선택하고 필요하면 나머지 단계를 직접 지정한다.

- '기본 후보'는 ``suggest_ladder``로 N 옆 칸을 채운다(사용자가 확인 후 저장).
- ``check_groups`` 결과를 막힘/경고로 보여 주고, 저장·이력·되돌리기는 저장 서비스(``GroupStore``)를 쓴다.
- 줄마다 ``tier_of`` 색 단계를 보여 준다. 그룹을 정하기 전(목표 1개)에도 같은 단계로 나온다.
- 저장한 그룹은 다음 '조회 적용'부터 계산에 쓴다. 이미 보이는 결과는 바꾸지 않는다.
"""

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QGridLayout, QHBoxLayout, QRadioButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, button, label
from setron.ui.group_data import (
    SLOT_LABELS, TIER_COLORS, TIER_LABELS, GroupSaveBlocked, Slot, VersionGroups, check_groups, group_store_for,
    release_tier, suggest_ladder, target_label,
)
from setron.ui.target_data import target_pairs
from setron.ui.theme import CANVAS, LINE, PRIMARY, SECONDARY, font
from setron.ui.version_filter_dialog import VersionChoice

SLOT_CHOICES = (None, Slot.N_MINUS_2, Slot.N_MINUS_1, Slot.N, Slot.N_PLUS_1, Slot.N_PLUS_2)
BLOCK_INK, WARN_INK = "#ad344d", "#8a6400"


class _PairChoice(QRadioButton):
    def hitButton(self, position):  # noqa: N802 - Qt API
        return self.rect().contains(position)


class VersionGroupDialog(QDialog):
    def __init__(self, source, *, targets=None, group_key=None, store=None, parent=None):
        super().__init__(parent)
        self.source = source
        self.store = store or group_store_for(source)
        self.pairs = target_pairs(source)
        self.single = ({(t.model_id, t.family_id): t.release_id for t in targets} if targets is not None
                       else {p.key: p.current_id for p in self.pairs})
        self.drafts: dict[tuple[int, int], dict[int, Slot]] = {}  # 모델·계열 → 릴리즈 ID → 칸
        self.saved_groups = {}
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
        body.addWidget(label("설비 모델·계열", 13, medium=True))
        self.pair_group = QButtonGroup(self)
        self.pair_buttons = {}
        self.pair_scroll = QScrollArea()
        self.pair_scroll.setAccessibleName("기준을 편집할 설비 모델·계열")
        self.pair_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.pair_scroll.setWidgetResizable(True)
        self.pair_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pair_list = QWidget()
        pair_list.setObjectName("canvas")
        choices = QVBoxLayout(pair_list)
        choices.setContentsMargins(0, 0, 4, 0)
        choices.setSpacing(4)
        for index, pair in enumerate(self.pairs):
            control = _PairChoice(pair.label)
            control.setToolTip(pair.label)
            control.setFont(font(13, medium=True))
            control.setFixedHeight(34)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            control.setStyleSheet(f"QRadioButton {{background:white;border:1px solid {LINE};border-radius:8px;padding:0 10px;spacing:8px;}}"
                                  f"QRadioButton:hover {{border-color:{PRIMARY};}}"
                                  f"QRadioButton:checked {{background:#edf0fd;border-color:{PRIMARY};color:{PRIMARY};}}"
                                  f"QRadioButton:focus {{border:2px solid {PRIMARY};}}")
            self.pair_group.addButton(control, index)
            self.pair_buttons[pair.key] = control
            choices.addWidget(control)
        self.pair_scroll.setWidget(pair_list)
        if self.pairs:
            self.pair_scroll.setFixedHeight(38 * min(len(self.pairs), 3) - 4)
            initial = next((p for p in self.pairs if p.group_key == group_key), self.pairs[0])
            self.pair_buttons[initial.key].setChecked(True)
            QTimer.singleShot(0, self, lambda: self.pair_scroll.ensureWidgetVisible(self.pair_buttons[self.pair.key], 0, 4))
        else:
            self.pair_scroll.hide()
        body.addWidget(self.pair_scroll)

        body.addWidget(label("버전을 클릭하면 기준 N을 선택하거나 해제할 수 있습니다. 여러 버전을 함께 선택할 수 있습니다.",
                             13, color=SECONDARY, wrap=True))

        tools = QHBoxLayout()
        self.suggest_button = button("기본 후보")
        self.suggest_button.setAutoDefault(False)
        self.suggest_button.setToolTip("선택한 N 기준의 앞뒤 버전으로 비어 있는 단계를 채웁니다.")
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
        self.n_count = label("", 13, medium=True, color=SECONDARY)
        tools.addWidget(self.n_count)
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
        self.history_card.body.addLayout(self.history)
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
        self.pair_group.idToggled.connect(self._pair_changed)
        self._render()
        width = min(760, parent.width() - 64) if parent is not None else 760
        height = min(780, parent.height() - 64) if parent is not None else 780
        self.resize(max(520, width), max(540, height))

    # --- 자료 ---
    @property
    def pair(self):
        index = self.pair_group.checkedId()
        return self.pairs[index] if index >= 0 else None

    def family(self, pair=None):
        pair = pair or self.pair
        return self.source.families.get(pair.family_id) if pair else None

    def _saved(self, pair):
        if pair.key in self.saved_groups:
            return self.saved_groups[pair.key]
        try:
            saved = self.store.load(pair.key)
        except Exception as error:  # 저장 형식 오류·DB 오류를 화면에 안내한다.
            self.status.setText(f"저장된 기준을 읽지 못했습니다: {error}")
            return None
        self.saved_groups[pair.key] = saved
        return saved

    def draft(self, pair=None) -> dict[int, Slot]:
        pair = pair or self.pair
        if pair.key not in self.drafts:
            saved = self._saved(pair)
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
        return self._saved(pair) is None and self.draft(pair) == ({target: Slot.N} if target is not None else {})

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

    def _pair_changed(self, _index, checked):
        if checked:
            self.status.clear()
            self._render()
            self.scroll.verticalScrollBar().setValue(0)

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
        has_pair = pair is not None
        self.suggest_button.setEnabled(has_pair)
        self.save_button.setEnabled(has_pair)
        if not has_pair:
            self.details_button.setEnabled(False)
            self.rows.addWidget(label("설정할 모델·계열이 없습니다.", 13, color=SECONDARY), 0, 0)
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
        self._render_history()

    def _refresh(self):
        """지금 초안의 선택 수·색 단계·확인 결과를 갱신한다."""
        pair, family = self.pair, self.family()
        groups = self.groups(pair)
        self.n_count.setText(f"기준 N {len(groups.members(Slot.N))}개 선택")
        single = self._uses_single(pair)
        for rid, chip in self.tier_labels.items():
            tier = release_tier(family, rid, None if single else groups, self.single.get(pair.key))
            background, ink = TIER_COLORS[tier]
            chip.setText(TIER_LABELS[tier])
            chip.setStyleSheet(f"background:{background};color:{ink};border-radius:4px;padding:0 6px;")
        self._clear(self.issues)
        found = check_groups(family, groups) if family is not None else ()
        for issue in found:
            line = label(("막힘 · " if issue.blocks else "경고 · ") + issue.message, 12,
                         color=BLOCK_INK if issue.blocks else WARN_INK, wrap=True)
            self.issues.addWidget(line)
        self.save_button.setEnabled(family is not None and not any(issue.blocks for issue in found))
        self.suggest_button.setEnabled(family is not None and groups.has_target)

    def _render_history(self):
        self._clear(self.history)
        pair, family = self.pair, self.family()
        saved = self._saved(pair)
        if saved is None:
            self.history.addWidget(label("저장 이력이 없습니다.", 12, color=SECONDARY))
            return
        entries = list(enumerate(saved.history))
        now = saved.history[-1].groups.to_dict()
        shown = [(index, entry) for index, entry in entries               # 예전에 쌓인 연속 중복은 가장 최근 한 줄만
                 if index == len(entries) - 1 or entry.groups.to_dict() != entries[index + 1][1].groups.to_dict()]
        for index, entry in reversed(shown):
            row = QHBoxLayout()
            current = index == len(entries) - 1
            text = f"{entry.saved_at:%Y-%m-%d %H:%M}" + (" · 지금 기준" if current else "") + \
                   (f" · {entry.memo}" if entry.memo else "") + f"\n{target_label(family, entry.groups)}"
            row.addWidget(label(text, 12, color=None if current else SECONDARY, wrap=True), 1)
            if not current:
                revert = button("되돌리기")
                revert.setAutoDefault(False)
                revert.setFixedHeight(30)
                revert.setAccessibleName(f"{entry.saved_at:%Y-%m-%d %H:%M} 기준으로 되돌리기")
                revert.clicked.connect(lambda checked=False, e=entry: self.revert(e))
                if entry.groups.to_dict() == now:                             # 지금 기준과 같으면 되돌릴 것이 없다
                    revert.setEnabled(False)
                    revert.setToolTip("지금 기준과 같습니다.")
                row.addWidget(revert, 0, Qt.AlignmentFlag.AlignTop)
            self.history.addLayout(row)
            divider = QFrame()
            divider.setFixedHeight(1)
            divider.setStyleSheet(f"background:{LINE};")
            self.history.addWidget(divider)

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
        groups = self.groups()
        if family is None or not groups.has_target:
            self.status.setText("N 칸을 먼저 고르면 옆 칸을 채울 수 있습니다.")
            return False
        filled = suggest_ladder(family, groups)
        self.drafts[self.pair.key] = {rid: slot for slot, ids in filled.slots.items() for rid in ids}
        self.status.clear()
        self._render()
        return True

    def _store_save(self, groups, memo):
        pair, family = self.pair, self.family()
        try:
            result = self.store.save(pair.key, groups, family, memo=memo)
        except GroupSaveBlocked as error:
            self.status.setText(f"저장하지 않았습니다: {error}")
            return False
        except Exception as error:  # DB 연결·형식 오류는 그대로 안내하고 초안은 유지한다.
            self.status.setText(f"저장하지 못했습니다: {error}")
            return False
        self.saved_groups[pair.key] = result.saved
        self.drafts[pair.key] = {rid: slot for slot, ids in result.saved.groups.slots.items() for rid in ids}
        self._render()
        if not getattr(result, "changed", True):
            self.status.setText("지금 기준과 같아 저장하지 않았습니다.")
            return True
        self.saved_any = True
        warned = f" · 경고 {len(result.warnings)}건" if result.warnings else ""
        self.status.setText(f"저장했습니다{warned}. '조회 적용'을 누르면 새 기준으로 계산합니다.")
        return True

    def save(self):
        return self._store_save(self.groups(), "")

    def revert(self, entry):
        return self._store_save(entry.groups, f"{entry.saved_at:%Y-%m-%d %H:%M} 기준으로 되돌림")
