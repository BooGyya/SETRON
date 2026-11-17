"""색상 팔레트의 Qt 편집기와 표시 테마를 연결한다."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QColorDialog, QDialogButtonBox, QProxyStyle, QStyle, QStyleFactory, QWidget

from setron.ui.appearance import readable_color, surface_edge


class _ColorButtonOrder(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_DialogButtonLayout:
            return QDialogButtonBox.ButtonLayout.MacLayout.value
        return super().styleHint(hint, option, widget, returnData)


class SettingsColorDialog(QColorDialog):
    def __init__(self, value, title, colors, parent):
        super().__init__(QColor(value), parent)
        self.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        self.setWindowTitle(f"{title} 색상 선택")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        if buttons := self.findChild(QDialogButtonBox):
            order = _ColorButtonOrder(QStyleFactory.create("Fusion"))
            order.setParent(buttons)
            buttons.setStyle(order)
            confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
            confirm.setText("확인")
            confirm.setProperty("paletteConfirm", True)
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        # 초안 테마가 바뀌는 중에도 팔레트의 화면색과 입력색은 같은 조합으로 유지한다.
        for widget in (self, *self.findChildren(QWidget)):
            widget.setProperty("themePreview", True)
        self._apply_colors(colors)

    def _apply_colors(self, colors):
        surface, field, selected = colors["surface"], colors["soft"], colors["selected"]
        ink, accent = colors["ink"], colors["accent"]
        surface_ink = readable_color(ink, surface)
        field_ink = readable_color(ink, field)
        selected_ink = readable_color(ink, selected)
        muted = readable_color(colors["muted"], field)
        border = readable_color(colors["line"], field, 3)
        focus = readable_color(accent, field, 3)
        primary = colors["primary"]
        primary_ink = readable_color(ink, primary)
        primary_border = surface_edge(primary, surface, border)
        primary_focus = readable_color(accent, primary, 3)

        palette = QPalette(self.palette())
        for role, color in {
            QPalette.ColorRole.Window: surface, QPalette.ColorRole.WindowText: surface_ink,
            QPalette.ColorRole.Base: field, QPalette.ColorRole.Text: field_ink,
            QPalette.ColorRole.Button: field, QPalette.ColorRole.ButtonText: field_ink,
            QPalette.ColorRole.Highlight: selected, QPalette.ColorRole.HighlightedText: selected_ink,
            QPalette.ColorRole.Light: border, QPalette.ColorRole.Dark: border,
        }.items():
            palette.setColor(role, QColor(color))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QColorDialog {{background:{surface};}}
            QLabel {{color:{surface_ink};background:transparent;}}
            QLineEdit, QSpinBox {{background:{field};color:{field_ink};border:1px solid {border};
                                  selection-background-color:{selected};selection-color:{selected_ink};}}
            QPushButton {{background:{field};color:{field_ink};border:1px solid {border};}}
            QPushButton:hover {{background:{selected};color:{selected_ink};}}
            QLineEdit:focus, QSpinBox:focus, QPushButton:focus {{border-color:{focus};}}
            QPushButton:disabled {{background:{field};color:{muted};border-color:{border};}}
            QPushButton[paletteConfirm="true"] {{background:{primary};color:{primary_ink};border-color:{primary_border};}}
            QPushButton[paletteConfirm="true"]:focus {{border-color:{primary_focus};}}
        """)
