"""개인 설정 저장과 화면 적용의 공통 진행 창. 업무 데이터는 접근하지 않는다."""

from PySide6.QtCore import QObject, QTimer, Signal

from setron.ui.import_dialogs import ImportProgressDialog
from setron.ui.read_worker import BackgroundRead, ReadRequest


class SettingsSave(QObject):
    changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.busy = False
        self.dialog = ImportProgressDialog(parent)
        self.request = ReadRequest(self)
        self.request.completed.connect(self._stored)

    def start(self, store, key, value, title, after_save, failed):
        if self.busy:
            return False
        self.busy = True
        self.after_save, self.failed = after_save, failed
        self.dialog.set_operation(key, title, "선택한 개인 설정을 저장하고 있습니다.")
        self.changed.emit()
        write = store.writer(key, value)
        # 팝업이 그려진 후 독립 QSettings 인스턴스로 디스크 쓰기를 시작한다.
        QTimer.singleShot(0, lambda: self.request.start(BackgroundRead(write)))
        return True

    def _stored(self, value, error):
        if error is not None:
            self.failed("개인 설정을 저장하지 못했습니다. 이전 설정을 유지합니다.")
            self._finish()
            return
        self.dialog.set_operation(self.dialog.operation, "화면에 적용 중", "저장한 설정을 화면에 적용하고 있습니다.")
        self.after_save(self._finish)

    def _finish(self):
        self.busy = False
        self.dialog.set_operation(None)
        self.changed.emit()
