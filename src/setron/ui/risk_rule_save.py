"""Item 위험 기준 저장을 화면 수명과 분리하는 비동기 조정자."""

from copy import deepcopy

from PySide6.QtCore import QObject, Signal

from setron.features.management import ManagementResult
from setron.features.release.refresh_schemas import RefreshContext
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseConnectionError, DatabaseSaveError
from setron.ui.read_worker import BackgroundRead, ReadRequest


class RiskRuleSaveController(QObject):
    save_started = Signal()
    saved = Signal(object)
    save_failed = Signal()
    save_uncertain = Signal()

    def __init__(self, gateway=None, *, context_provider=None, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self.context_provider = context_provider or (lambda: RefreshContext())
        self.state, self.locked = "idle", False
        self.operation_error = None
        self.request = ReadRequest(self)
        self.request.completed.connect(self._completed)

    @property
    def busy(self):
        return self.request.busy

    def create_risk_rule(self, values):
        try:
            values = deepcopy(values)
        except Exception:
            return False
        return self._start(lambda context: self.gateway.create_risk_rule(values, context))

    def set_risk_rule_enabled(self, risk_rule_id, enabled):
        """위험 기준 해제(False)·다시 사용(True). 등록과 같은 상태·신호·갱신 경로를 쓴다."""
        return self._start(lambda context: self.gateway.set_risk_rule_enabled(risk_rule_id, enabled, context))

    def _start(self, call):
        if self.busy or self.locked or self.state == "saving" or self.gateway is None:
            return False
        try:
            context = deepcopy(self.context_provider())
        except Exception:
            return False
        self.state, self.operation_error = "saving", None
        self.save_started.emit()
        return self.request.start(BackgroundRead(lambda: call(context)))

    def _completed(self, value, error):
        self.operation_error = error
        known_failure = isinstance(error, (ValueError, LookupError, DatabaseConnectionError, DatabaseSaveError))
        if error is not None and not isinstance(error, DatabaseCommitUnknownError) and known_failure:
            self.state = "failed"
            self.save_failed.emit()
        elif error is not None or not isinstance(value, ManagementResult):
            self.state, self.locked = "unknown", True
            self.save_uncertain.emit()
        else:
            self.state = "idle"
            self.saved.emit(value)
