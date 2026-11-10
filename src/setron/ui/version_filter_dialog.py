"""Figma VER5 / 2-V: 조회용 버전 다중 선택. 완료 전까지 공통 초안도 변경하지 않는다."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QGridLayout, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from setron.ui.components import button, label
from setron.ui.query_state import VersionFilter
from setron.ui.theme import SECONDARY, font, set_widget_font


def choice_caption(value):
    """원문은 합치지 않되, 앞뒤 공백 때문에 같은 글자로 보이는 버전을 구분해 보여 준다 (QA R-05)."""
    if value == value.strip():
        return value
    places = [name for name, present in (("앞", value != value.lstrip()), ("끝", value != value.rstrip())) if present]
    return f"{value.strip()}  ({'·'.join(places)} 공백 포함 원문)"


class VersionChoice(QCheckBox):
    """긴 원문도 줄바꿈하며 체크박스의 Space·포커스·접근성 역할을 유지한다."""

    def __init__(self, text):
        super().__init__()
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
    def __init__(self, options, original=None, *, is_current=lambda: True, parent=None):
        super().__init__(parent)
        self.options, self.original, self.is_current = tuple(options), original, is_current
        self.result_filter = None
        self.choices = {}
        self.columns = 0
        self.setWindowTitle("SETRON · 버전 다중 선택 조회")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(420, 360)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("QDialog {background:white;}")
        body = QVBoxLayout(self)
        body.setContentsMargins(20, 20, 20, 20)
        body.setSpacing(16)
        body.addWidget(label("버전 다중 선택 조회", 22, medium=True, wrap=True))
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.all_button = button("전체 선택")
        self.clear_button = button("선택 해제")
        for control in (self.all_button, self.clear_button):
            control.setAutoDefault(False)
            control.setFixedSize(120, 40)
            tools.addWidget(control)
        self.count = label("", 13)
        self.count.setAccessibleName("선택한 버전 개수")
        tools.addWidget(self.count)
        tools.addStretch()
        body.addLayout(tools)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("versionFilterOptions")
        content.setStyleSheet("QWidget#versionFilterOptions {background:white;}")
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        # 사라진 원문도 선택 해제 전까지 보존한다. 없어졌다는 이유로 전체 조회로 넓히지 않는다.
        extra = () if original is None else (*original.versions, *((None,) if original.include_missing else ()))
        values = tuple(dict.fromkeys((*self.options, *extra)))
        for value in values:
            control = VersionChoice("버전 미등록 포함" if value is None else choice_caption(value))
            control.setChecked(original is None or (original.include_missing if value is None else value in original.versions))
            if value not in self.options:
                control.setToolTip(control.toolTip() + " · 현재 관측 자료에 없음")
            control.toggled.connect(self._review)
            self.choices[value] = control
        self.scroll.setWidget(content)
        body.addWidget(self.scroll, 1)
        self.error = label("", 12, color="#ad344d", wrap=True)
        self.error.setAccessibleName("버전 선택 오류")
        body.addWidget(self.error)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = button("취소")
        self.cancel_button.setFixedSize(120, 40)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = button("선택 완료", primary=True)
        self.confirm_button.setFixedSize(160, 40)
        self.confirm_button.clicked.connect(self.accept)
        for control in (self.cancel_button, self.confirm_button):
            control.setAutoDefault(False)
            actions.addWidget(control)
        body.addLayout(actions)
        self.all_button.clicked.connect(lambda: self._set_all(True))
        self.clear_button.clicked.connect(lambda: self._set_all(False))
        self.resize(min(720, max(420, parent.width() - 48)) if parent else 720,
                    min(560, max(360, parent.height() - 48)) if parent else 560)
        self._arrange()
        self._review()

    def resizeEvent(self, event):
        self._arrange()
        super().resizeEvent(event)

    def _arrange(self):
        columns = 2 if self.width() >= 600 else 1
        if columns == self.columns:
            return
        for control in self.choices.values():
            self.grid.removeWidget(control)
        for column in range(2):
            self.grid.setColumnStretch(column, int(column < columns))
        for index, control in enumerate(self.choices.values()):
            self.grid.addWidget(control, index // columns, index % columns)
        self.columns = columns

    def _set_all(self, checked):
        for control in self.choices.values():
            previous = control.blockSignals(True)
            control.setChecked(checked)
            control.blockSignals(previous)
        self._review()

    def _review(self, *_):
        count = sum(control.isChecked() for control in self.choices.values())
        self.count.setText(f"선택 중  {count}개")
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
