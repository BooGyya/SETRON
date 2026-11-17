"""7번: VER3 목표 릴리즈 선택을 모델·계열별 공통 편집 대화상자로 확장한다."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QHBoxLayout, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.query_state import TargetN
from setron.ui.target_data import initial_targets, target_pairs, validate_targets
from setron.ui.theme import CANVAS, PRIMARY, SECONDARY


class TargetChoiceButton(QPushButton):
    def __init__(self, text):
        super().__init__()
        self.setCheckable(True)
        self.setAutoDefault(False)
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setStyleSheet("QPushButton {background:transparent;border:1px solid transparent;border-radius:10px;padding:0;min-height:0;}"
                           "QPushButton:checked {background:#f7f9fc;border-color:#e5eaf3;}"
                           f"QPushButton:focus {{border:2px solid {PRIMARY};}}"
                           "QPushButton:disabled {color:#8b95a8;}")
        body = QVBoxLayout(self)
        body.setContentsMargins(18, 8, 18, 8)
        self.caption = label(text, 14, medium=True, color=SECONDARY, wrap=True)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        body.addWidget(self.caption)

    def minimumSizeHint(self):
        return QSize(0, self.layout().minimumSize().height())

    def sizeHint(self):
        return self.layout().sizeHint()


class TargetDialog(QDialog):
    def __init__(self, source, targets=None, *, group_key=None, is_current=lambda: True, parent=None):
        super().__init__(parent)
        self.source, self.is_current = source, is_current
        self.pairs = target_pairs(source)
        self.original = initial_targets(source) if targets is None else tuple(targets)
        self.draft = {(t.model_id, t.family_id): t.release_id for t in self.original}
        self.result_targets = None
        self.option_buttons = {}
        self.setWindowTitle("SETRON · 목표 기준 N 편집")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(420, 400)
        self.setSizeGripEnabled(True)
        self.setStyleSheet(f"QDialog {{background:{CANVAS};}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(16)
        body.addWidget(label("목표 기준 N 편집", 24, medium=True, wrap=True))
        self.notice = StatusBadge("앱 실행 중 임시 선택 · DB 저장 없음", "info")
        body.addWidget(self.notice)
        body.addWidget(label("확인된 릴리즈를 모델·계열별로 선택합니다. 다음 조회에 선택한 기준을 반영합니다.",
                             13, color=SECONDARY, wrap=True))
        self.pair_combo = ComboBox()
        self.pair_combo.setProperty("queryField", True)
        self.pair_combo.setAccessibleName("목표를 편집할 설비군·모델·계열")
        self.pair_combo.setMinimumWidth(0)
        self.pair_combo.setMinimumContentsLength(8)
        self.pair_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pair_combo.setFixedHeight(38)
        for pair in self.pairs:
            self.pair_combo.addItem(pair.label)
        initial_index = next((i for i, pair in enumerate(self.pairs) if pair.group_key == group_key), 0)
        self.pair_combo.setCurrentIndex(initial_index)
        body.addWidget(self.pair_combo)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("canvas")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.choice_card = Card(padding=12)
        self.choice_card.setStyleSheet("QFrame#card {background:white;border:1px solid #e5eaf3;border-radius:12px;}")
        self.choice_card.body.setSpacing(8)
        self.choice_heading = label("목표 기준 N", 13, medium=True, wrap=True)
        self.choice_card.body.addWidget(self.choice_heading)
        self.option_host = QWidget()
        self.options_layout = QVBoxLayout(self.option_host)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(8)
        self.choice_card.body.addWidget(self.option_host)
        layout.addWidget(self.choice_card)
        self.review = label("", 13, color=SECONDARY, wrap=True)
        layout.addWidget(self.review)
        layout.addStretch()
        self.scroll.setWidget(content)
        body.addWidget(self.scroll, 1)
        self.error = label("", 13, color="#ad344d", wrap=True)
        self.error.setAccessibleName("목표 선택 오류")
        body.addWidget(self.error)
        actions = QHBoxLayout()
        self.reset_button = button("선택 원래대로")
        self.reset_button.setAutoDefault(False)
        self.reset_button.clicked.connect(self.reset_draft)
        self.cancel_button = button("취소")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = button("선택 완료", primary=True)
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.accept)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.confirm_button)
        body.addLayout(actions)
        self.pair_combo.currentIndexChanged.connect(self._render_options)
        self._render_options()
        width = min(680, parent.width() - 64) if parent is not None else 680
        height = min(720, parent.height() - 64) if parent is not None else 720
        if screen := self.screen():
            width, height = min(width, screen.availableGeometry().width() - 32), min(height, screen.availableGeometry().height() - 32)
        self.resize(max(420, width), max(400, height))

    def values(self):
        return tuple(TargetN(mid, fid, rid) for (mid, fid), rid in sorted(self.draft.items()))

    def _render_options(self, *_):
        self.option_buttons.clear()
        while self.options_layout.count():
            widget = self.options_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        if hasattr(self, "choice_group"):
            self.choice_group.deleteLater()
        self.choice_group = QButtonGroup(self)
        self.error.clear()
        self.pair_combo.setEnabled(bool(self.pairs))
        if not self.pairs:
            self.choice_heading.setText("설정할 모델·계열이 없습니다.")
            self._review()
            return
        pair = self.pairs[self.pair_combo.currentIndex()]
        self.choice_heading.setText(pair.label + " · 목표 기준 N")
        original = {(t.model_id, t.family_id): t.release_id for t in self.original}.get(pair.key)
        for rid, text, reason in ((None, "목표 미지정 · 판단 불가로 표시", None), *(
            (option.release.release_version_id, option.release.version_raw,
             option.unavailable_reason) for option in pair.options)):
            caption = text + (" · 기존 선택" if rid == original else "") + (f" · {reason} (선택 불가)" if reason else "")
            control = TargetChoiceButton(caption)
            control.setEnabled(reason is None)
            control.setChecked(rid == self.draft.get(pair.key))
            self.choice_group.addButton(control)
            control.clicked.connect(lambda checked=False, key=pair.key, release_id=rid: self.choose(key, release_id))
            self.options_layout.addWidget(control)
            self.option_buttons[rid] = control
        if not pair.options:
            self.options_layout.addWidget(label("이 계열에 등록된 릴리즈가 없습니다. 임의의 버전을 입력하거나 추정하지 않습니다.", 13, color=SECONDARY, wrap=True))
        eligible = {o.release.release_version_id for o in pair.options if o.unavailable_reason is None}
        if self.draft.get(pair.key) is not None and self.draft[pair.key] not in eligible:
            self.options_layout.addWidget(label("현재 목표의 연결·순서 확인이 필요합니다. 다른 릴리즈로 자동 변경하지 않았습니다.",
                                                13, color="#856009", wrap=True))
        self._review()

    def choose(self, key, release_id):
        pair = next((p for p in self.pairs if p.key == key), None)
        if pair is None or (release_id is not None and not any(
            o.release.release_version_id == release_id and o.unavailable_reason is None for o in pair.options)):
            self.error.setText("같은 모델·계열의 순서가 확인된 릴리즈만 선택할 수 있습니다.")
            return False
        self.draft[key] = release_id
        if self.pairs and self.pairs[self.pair_combo.currentIndex()].key == key and release_id in self.option_buttons:
            self.option_buttons[release_id].setChecked(True)
        self.error.clear()
        self._review()
        if self.pairs and self.pairs[self.pair_combo.currentIndex()].key == key and release_id in self.option_buttons:
            self.scroll.ensureWidgetVisible(self.option_buttons[release_id], 0, 12)
        return True

    def _review(self):
        original = {(t.model_id, t.family_id): t.release_id for t in self.original}
        changed = [pair for pair in self.pairs if self.draft.get(pair.key) != original.get(pair.key)]
        lines = [f"변경 {len(changed)}개 / 전체 {len(self.pairs)}개 모델·계열 · 조회 적용 전"]
        for pair in changed:
            def text(rid):
                return "미지정" if rid is None else next((o.release.version_raw for o in pair.options if o.release.release_version_id == rid), "릴리즈 미확인")
            lines.append(f"{pair.label}\n{text(original.get(pair.key))} → {text(self.draft[pair.key])}")
        self.review.setText("\n".join(lines))
        self.confirm_button.setEnabled(bool(changed))
        self.reset_button.setEnabled(bool(changed))

    def reset_draft(self):
        self.draft = {(t.model_id, t.family_id): t.release_id for t in self.original}
        self._render_options()

    def accept(self):
        try:
            if not self.is_current() or target_pairs(self.source) != self.pairs:
                self.error.setText("선택지·릴리즈 자료가 변경되었습니다. 취소 후 다시 열어 주세요.")
                return
            self.result_targets = validate_targets(self.source, self.values())
        except ValueError as error:
            self.error.setText(str(error))
            return
        super().accept()
