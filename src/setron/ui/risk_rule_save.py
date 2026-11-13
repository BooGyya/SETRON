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
        return self._start("create_risk_rule", values)

    def update_risk_rule(self, record, values):
        return self._start("update_risk_rule", record, values)

    def delete_risk_rule(self, record):
        return self._start("delete_risk_rule", record)

    def set_risk_rule_enabled(self, risk_rule_id, enabled):
        """해제·다시 사용도 동일한 저장 상태와 완료·실패 신호를 사용한다."""
        return self._start("set_risk_rule_enabled", risk_rule_id, enabled)

    def _start(self, method, *arguments):
        if self.busy or self.locked or self.state == "saving" or self.gateway is None:
            return False
        try:
            operation = getattr(self.gateway, method)
            context, arguments = deepcopy(self.context_provider()), deepcopy(arguments)
        except Exception:
            return False
        self.state, self.operation_error = "saving", None
        self.save_started.emit()
        return self.request.start(BackgroundRead(lambda: operation(*arguments, context)))

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
