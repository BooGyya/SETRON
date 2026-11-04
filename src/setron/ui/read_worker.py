"""명시적으로 지정한 읽기 공급자만 워커에서 실행하는 공통 기반."""

from dataclasses import dataclass
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot


@dataclass(frozen=True)
class BackgroundRead:
    """백그라운드 실행을 허용한 읽기 공급자.

    ivar
    - load: Qt 위젯을 접근하지 않고 독립된 읽기 결과를 반환하는 함수.
    """

    load: Callable

    def __call__(self):
        """원래 공급자를 호출한다.

        return
        - 공급자의 읽기 결과.

        raises
        - Exception: 공급자가 실패한 경우 그대로 전달.
        """
        return self.load()


class _Result(QObject):
    """워커 완료 신호 전달 객체."""

    done = Signal(int, object, object)


class _ReadTask(QRunnable):
    """화면 객체를 참조하지 않는 읽기 작업."""

    def __init__(self, generation, loader):
        """작업과 독립된 완료 신호를 보관한다.

        param
        - generation: 요청 세대 번호.
        - loader: 읽기 공급자.
        """
        super().__init__()
        self.generation, self.loader = generation, loader
        self.result = _Result()

    def run(self):
        """성공 또는 실패를 UI 스레드에 전달한다."""
        try:
            value = self.loader()
        except Exception as error:
            self.result.done.emit(self.generation, None, error)
        else:
            self.result.done.emit(self.generation, value, None)


class ReadRequest(QObject):
    """중복 요청과 취소된 응답을 차단하는 조회 조정자."""

    completed = Signal(object, object)

    def __init__(self, parent):
        """화면 수명에 맞춰 조정자를 생성한다.

        param
        - parent: 결과를 사용하는 화면. 삭제 시 완료 연결도 해제된다.
        """
        super().__init__(parent)
        self.busy, self.generation = False, 0

    def start(self, loader):
        """명시한 워커 공급자 또는 기존 메모리 공급자를 실행한다.

        param
        - loader: BackgroundRead 또는 즉시 반환하는 기존 메모리 공급자.

        return
        - 시작했으면 True, 진행 중 요청이 있으면 False.
        """
        if self.busy:
            return False
        self.busy = True
        self.generation += 1
        task = _ReadTask(self.generation, loader)
        if isinstance(loader, BackgroundRead):
            task.result.done.connect(self._finish, Qt.ConnectionType.QueuedConnection)
            QThreadPool.globalInstance().start(task)
        else:
            task.result.done.connect(self._finish)
            task.run()
        return True

    @Slot(int, object, object)
    def _finish(self, generation, value, error):
        """현재 요청의 결과만 화면으로 전달한다.

        param
        - generation: 완료한 요청 번호.
        - value: 조회 결과 또는 None.
        - error: 공급자 예외 또는 None.
        """
        if not self.busy or generation != self.generation:
            return
        self.busy = False
        self.completed.emit(value, error)

    def cancel(self):
        """응답 수락을 취소한다. 실행 중인 공급자 스레드는 강제 종료하지 않는다."""
        self.generation += 1
        self.busy = False
