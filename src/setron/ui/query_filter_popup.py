"""공통 조회 조건의 검색 가능한 다중 선택 팝업."""

from PySide6.QtCore import QPoint, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from setron.ui.components import button, label
from setron.ui.theme import ACCENT_SOFT, ASSETS, LINE, PRIMARY, SECONDARY, SOFT, icon
from setron.ui.version_filter_dialog import VersionChoice
from setron.ui.view_controls import bulk_selection_button


class QueryFilterPopup(QDialog):
    """체크 초안을 팝업 안에 보관하고 선택 완료 시 호출자가 반영한다."""

    selection_changed = Signal(object)

    def __init__(self, title, options, selected, *, parent, anchor=None, show_groups=True):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.anchor = parent if anchor is None else anchor
        self.setObjectName("queryFilterPopup")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setStyleSheet(f"QDialog#queryFilterPopup {{background:white;border:1px solid {LINE};border-radius:10px;}}")
        self.selected_keys = ()
        self.checks = {}
        self._sections = {}
        selected = set(selected)

        body = QVBoxLayout(self)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(label(f"{title} 선택", 15, medium=True, wrap=True), 1)
        self.count = label("", 12, color=SECONDARY)
        self.count.setAccessibleName("선택한 항목 개수")
        header.addWidget(self.count, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(header)
        self.search = QLineEdit()
        self.search.setAccessibleName(f"{title} 검색")
        self.search.setPlaceholderText(f"{title} 검색")
        self.search.addAction(icon("b2c35.svg"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(36)
        self.search.setStyleSheet(f"QLineEdit {{background:{SOFT};border:1px solid {LINE};border-radius:8px;padding:0 10px;}}"
                                  f"QLineEdit:focus {{border:2px solid {PRIMARY};}}")
        body.addWidget(self.search)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        choices = QVBoxLayout(content)
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setSpacing(4)
        choices.setAlignment(Qt.AlignmentFlag.AlignTop)
        tick = (ASSETS / "checkbox-tick.svg").as_posix()
        checkbox_style = (f"QCheckBox {{border:1px solid transparent;border-radius:6px;padding-left:5px;}}"
                          f"QCheckBox:hover {{background:{SOFT};}} QCheckBox:checked {{background:{ACCENT_SOFT};}}"
                          f"QCheckBox:focus {{border-color:{PRIMARY};}}"
                          f"QCheckBox::indicator {{width:16px;height:16px;border:1px solid {LINE};border-radius:4px;background:white;}}"
                          f"QCheckBox::indicator:checked {{border-color:#3655c7;background:#3655c7;image:url({tick});}}")
        for key, text, group in options:
            section_key = group if show_groups else None
            if section_key not in self._sections:
                section = QWidget()
                section_body = QVBoxLayout(section)
                section_body.setContentsMargins(0, 0, 0, 0)
                section_body.setSpacing(0)
                if section_key is not None:
                    section_body.addWidget(label(section_key, 12, medium=True, color=SECONDARY, wrap=True))
                choices.addWidget(section)
                self._sections[section_key] = section, []
            section, entries = self._sections[section_key]
            check = VersionChoice(text)
            check.setChecked(not selected or key in selected)
            check.setStyleSheet(checkbox_style)
            check.toggled.connect(self._review)
            section.layout().addWidget(check)
            entries.append((f"{group or ''} {text}".casefold(), check))
            self.checks[key] = check
        self.empty = label("선택 가능한 항목이 없습니다." if not self.checks else "검색 결과가 없습니다.",
                           12, color=SECONDARY, wrap=True)
        choices.addWidget(self.empty)
        scroll.setWidget(content)
        body.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self.all_button = bulk_selection_button("전체 선택")
        self.all_button.clicked.connect(self._toggle_all)
        self.confirm_button = button("선택 완료", primary=True)
        self.confirm_button.clicked.connect(self.accept)
        for control in (self.all_button, self.confirm_button):
            control.setAutoDefault(False)
            control.setFixedHeight(38)
            footer.addWidget(control, 1)
        body.addLayout(footer)
        self.search.textChanged.connect(self._filter)
        self._filter("")
        self._review()

    def open(self):
        self.show()  # Qt.Popup이 바깥 클릭을 처리하도록 창 모달을 추가하지 않는다.

    def showEvent(self, event):  # noqa: N802 - Qt API
        anchor = self.anchor
        bounds = anchor.screen().availableGeometry()
        width = min(max(anchor.width(), 300), bounds.width())
        origin = anchor.mapToGlobal(QPoint(0, 0))
        below_top = max(origin.y() + anchor.height() + 4, bounds.top())
        above_bottom = min(origin.y() - 4, bounds.bottom() + 1)
        below = max(0, bounds.bottom() + 1 - below_top)
        above = max(0, above_bottom - bounds.top())
        desired = min(440, max(220, self.sizeHint().height()))
        use_above = below < self.minimumSizeHint().height() and above > below
        height = min(desired, above if use_above else below)
        top = above_bottom - height if use_above else below_top
        left = min(max(origin.x(), bounds.left()), bounds.right() + 1 - width)
        self.setGeometry(left, top, width, height)
        super().showEvent(event)
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        if not self.rect().contains(event.position().toPoint()):
            self.reject()
        else:
            super().mousePressEvent(event)

    def _filter(self, value):
        term = value.strip().casefold()
        found = False
        for section, entries in self._sections.values():
            visible = False
            for text, check in entries:
                matches = term in text
                check.setVisible(matches)
                visible |= matches
            section.setVisible(visible)
            found |= visible
        self.empty.setVisible(not found)

    def _toggle_all(self):
        checked = not all(check.isChecked() for check in self.checks.values())
        for check in self.checks.values():
            with QSignalBlocker(check):
                check.setChecked(checked)
        self._review()

    def _review(self, *_):
        selected = tuple(key for key, check in self.checks.items() if check.isChecked())
        count = len(selected)
        self.all_button.setText("전체 해제" if count and count == len(self.checks) else "전체 선택")
        self.all_button.setEnabled(bool(self.checks))
        self.count.setText("하나 이상 선택해 주세요" if not count and self.checks else f"{count}개 선택 중")
        self.confirm_button.setEnabled(count > 0)
        self.selected_keys = () if count == len(self.checks) else selected
        if selected:
            self.selection_changed.emit(self.selected_keys)

    def accept(self):
        if self.confirm_button.isEnabled():
            super().accept()
