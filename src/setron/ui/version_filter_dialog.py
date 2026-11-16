"""Figma VER5 / 2-V: 조회용 버전 다중 선택. 완료 전까지 공통 초안도 변경하지 않는다."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from setron.ui.components import button, label
from setron.ui.combo_box import ComboBox
from setron.ui.query_state import VersionFilter
from setron.ui.theme import SECONDARY, font, set_widget_font
from setron.ui.view_controls import bulk_selection_button


def choice_caption(value):
    """원문은 합치지 않되, 앞뒤 공백 때문에 같은 글자로 보이는 버전을 구분해 보여 준다 (QA R-05)."""
    if value == value.strip():
        return value
    places = [name for name, present in (("앞", value != value.lstrip()), ("끝", value != value.rstrip())) if present]
    return f"{value.strip()}  ({'·'.join(places)} 공백 포함 원문)"


class VersionChoice(QCheckBox):
    """긴 원문도 줄바꿈하며 체크박스의 Space·포커스·접근성 역할을 유지한다."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setAccessibleName(text)
        self.setToolTip(text)
        set_widget_font(self, font(14, medium=True))
        self.setMinimumHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        body = QVBoxLayout(self)
        body.setContentsMargins(28, 8, 12, 8)
        self.caption = label(text, 14, medium=True, color=SECONDARY, wrap=True)
        self.caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        body.addWidget(self.caption)

    def hitButton(self, position):  # noqa: N802 - Qt API
        return self.rect().contains(position)

    def minimumSizeHint(self):
        return QSize(0, max(40, self.layout().minimumSize().height()))

    def sizeHint(self):
        return self.layout().sizeHint().expandedTo(QSize(0, 40))


class VersionFilterDialog(QDialog):
    def __init__(self, options, original=None, *, group_options=(), is_current=lambda: True, parent=None):
        super().__init__(parent)
        self.options, self.original, self.is_current = tuple(options), original, is_current
        self.result_filter = None
        self.choices = {}
        group_options = tuple(group_options)
        self.group_versions = {key: frozenset(values) for key, _label, values in group_options}
        self.columns = 0
        self.setWindowTitle("SETRON · 조회 버전 설정")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(420, 360)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("QDialog {background:white;}")
        body = QVBoxLayout(self)
        body.setContentsMargins(20, 20, 20, 20)
        body.setSpacing(16)
        body.addWidget(label("조회 버전 설정", 22, medium=True, wrap=True))
        group_row = QHBoxLayout()
        group_row.setSpacing(12)
        group_row.addWidget(label("설비군", 13, medium=True, color=SECONDARY))
        self.group_filter = ComboBox()
        self.group_filter.setProperty("queryField", True)
        self.group_filter.setAccessibleName("조회 버전 설비군 필터")
        self.group_filter.setFixedHeight(40)
        self.group_filter.addItem("전체 설비군", None)
        for key, name, _values in group_options:
            self.group_filter.addItem(name, key)
        group_row.addWidget(self.group_filter, 1)
        body.addLayout(group_row)
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(8)
        self.all_button = bulk_selection_button("전체 선택")
        self.all_button.setAutoDefault(False)
        self.all_button.setFixedSize(120, 40)
        self.all_button.clicked.connect(self._toggle_all)
        tools.addWidget(self.all_button)
        tools.addStretch()
        self.count = label("", 13)
        self.count.setAccessibleName("선택한 버전 개수")
        tools.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(tools)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("versionFilterOptions")
        content.setStyleSheet("QWidget#versionFilterOptions {background:white;}")
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(12, 0, 12, 0)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        # 사라진 원문도 선택 해제 전까지 보존한다. 없어졌다는 이유로 전체 조회로 넓히지 않는다.
        extra = () if original is None else (*original.versions, *((None,) if original.include_missing else ()))
        values = tuple(dict.fromkeys((*self.options, *extra)))
        for value in values:
            # 최초 배치에서 표시해도 독립 창이 생기지 않도록 목록에 먼저 소속시킨다.
            control = VersionChoice("버전 미등록 포함" if value is None else choice_caption(value), parent=content)
            control.setChecked(original is None or (original.include_missing if value is None else value in original.versions))
            if value not in self.options:
                control.setToolTip(control.toolTip() + " · 현재 관측 자료에 없음")
            control.toggled.connect(self._review)
            self.choices[value] = control
        self.scroll.setWidget(content)
        body.addWidget(self.scroll, 1)
        self.empty_group = label("선택한 설비군의 조회 버전이 없습니다.", 12, color=SECONDARY, wrap=True)
        self.empty_group.hide()
        body.addWidget(self.empty_group)
        self.error = label("", 12, color="#ad344d", wrap=True)
        self.error.setAccessibleName("버전 선택 오류")
        body.addWidget(self.error)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch()
        self.cancel_button = button("취소")
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = button("확인", primary=True)
        self.confirm_button.clicked.connect(self.accept)
        for control in (self.cancel_button, self.confirm_button):
            control.setAutoDefault(False)
            control.setFixedSize(120, 40)
            actions.addWidget(control)
        body.addLayout(actions)
        self.resize(min(720, max(420, parent.width() - 48)) if parent else 720,
                    min(560, max(360, parent.height() - 48)) if parent else 560)
        self._arrange()
        self._review()
        self.group_filter.currentIndexChanged.connect(self._filter_groups)

    def resizeEvent(self, event):
        self._arrange()
        super().resizeEvent(event)

    def _visible_choices(self):
        key = self.group_filter.currentData()
        allowed = self.group_versions.get(key, ()) if key is not None else self.choices
        return tuple((value, control) for value, control in self.choices.items() if value in allowed)

    def _filter_groups(self, *_):
        self._arrange(force=True)
        self.scroll.verticalScrollBar().setValue(0)
        self._review()

    def _arrange(self, *, force=False):
        columns = 2 if self.width() >= 600 else 1
        if columns == self.columns and not force:
            return
        visible = dict(self._visible_choices())
        for value, control in self.choices.items():
            self.grid.removeWidget(control)
            control.setVisible(value in visible)
        for column in range(2):
            self.grid.setColumnStretch(column, int(column < columns))
        for index, control in enumerate(visible.values()):
            self.grid.addWidget(control, index // columns, index % columns)
        self.columns = columns

    def _toggle_all(self):
        visible = self._visible_choices()
        checked = not all(control.isChecked() for _value, control in visible)
        for _value, control in visible:
            previous = control.blockSignals(True)
            control.setChecked(checked)
            control.blockSignals(previous)
        self._review()

    def _review(self, *_):
        count = sum(control.isChecked() for control in self.choices.values())
        visible = self._visible_choices()
        self.all_button.setText("전체 해제" if visible and all(control.isChecked() for _value, control in visible) else "전체 선택")
        self.all_button.setEnabled(bool(visible))
        self.empty_group.setVisible(bool(self.choices) and not visible)
        self.count.setText(f"{count}개 선택 중")
        self.count.setToolTip(f"현재 설비군에 {len(visible)}개 표시 · 모든 설비군에서 선택한 버전을 유지합니다.")
        self.confirm_button.setEnabled(count > 0)
        self.error.setText("조회할 버전을 하나 이상 선택해 주세요." if self.choices and not count else
                           "저장된 버전 자료가 없습니다." if not self.choices else "")

    def accept(self):
        if not self.is_current():
            self.error.setText("조회 조건·버전 자료가 바뀌었습니다. 취소 후 다시 열어 주세요.")
            return
        selected = {value for value, control in self.choices.items() if control.isChecked()}
        if not selected:
            self._review()
            return
        self.result_filter = (None if selected == set(self.options) else
                              VersionFilter(tuple(value for value in selected if value is not None), None in selected))
        super().accept()
