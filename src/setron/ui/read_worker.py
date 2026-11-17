"""명시적으로 지정한 읽기 공급자만 워커에서 실행하는 공통 기반."""

import gc
from dataclasses import dataclass
from collections.abc import Callable
from functools import partial
from threading import Lock

from PySide6.QtCore import QObject, QThreadPool, QTimer, Qt, Signal, Slot


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


def _read(loader):
    """공급자를 실행해 (결과, 예외) 쌍으로 돌려준다."""
    try:
        return loader(), None
    except Exception as error:
        return None, error


_running_lock = Lock()
_running = 0


def running_reads():
    """지금 워커에서 실행 중인 백그라운드 읽기 수."""
    with _running_lock:
        return _running


def _change_running(delta):
    global _running
    with _running_lock:
        _running += delta


def _run_task(generation, loader, done):
    """워커 스레드에서 공급자를 실행하고 성공 또는 실패를 UI 스레드에 전달한다.

    작업마다 만든 Qt 객체로 결과를 보내면 그 객체가 작업 도중 GC로 먼저 삭제되어 결과가 사라질 수 있다.
    그래서 일반 함수로 실행하고, 결과는 화면과 수명이 같은 ReadRequest의 신호로 보낸다.

    param
    - generation: 요청 세대 번호.
    - loader: 읽기 공급자.
    - done: 결과를 UI 스레드로 보낼 ReadRequest의 내부 신호.
    """
    try:
        value, error = _read(loader)
        try:
            done.emit(generation, value, error)
        except RuntimeError:
            # 결과를 받을 화면(ReadRequest)이 먼저 삭제되었으면 결과를 버린다.
            pass
    finally:
        _change_running(-1)


def collect_garbage_when_idle(parent, interval_ms=1000):
    """자동 GC를 끄고, 백그라운드 읽기가 없을 때만 UI 스레드에서 GC를 실행한다.

    워커가 DB 구조를 읽는 동안 UI 스레드의 GC가 이전 DB 구조 객체를 지우면 앱이 강제 종료되는
    경우가 있었다(2026-11-17 시연 리허설). GC 시점을 워커와 겹치지 않는 때로 옮긴다.

    param
    - parent: 타이머 수명을 맡을 Qt 객체(보통 QApplication).
    - interval_ms: 확인 간격(밀리초).

    return
    - 자동 GC를 원래 상태로 되돌리는 함수. 앱이 끝날 때 호출한다.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    timer = QTimer(parent)
    timer.timeout.connect(lambda: gc.collect() if running_reads() == 0 else None)
    timer.start(interval_ms)

    def restore():
        timer.stop()
        if was_enabled:
            gc.enable()

    return restore


class ReadRequest(QObject):
    """중복 요청과 취소된 응답을 차단하는 조회 조정자."""

    completed = Signal(object, object)
    _done = Signal(int, object, object)

    def __init__(self, parent):
        """화면 수명에 맞춰 조정자를 생성한다.

        param
        - parent: 결과를 사용하는 화면. 삭제 시 완료 연결도 해제된다.
        """
        super().__init__(parent)
        self.busy, self.generation = False, 0
        # 워커 결과는 화면과 수명이 같은 이 객체의 신호로 받는다. 작업마다 신호 객체를 만들면
        # 작업 도중 GC로 먼저 삭제되어 결과가 사라지거나 앱이 강제 종료될 수 있다.
        self._done.connect(self._finish, Qt.ConnectionType.QueuedConnection)

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
        if isinstance(loader, BackgroundRead):
            _change_running(1)
            QThreadPool.globalInstance().start(partial(_run_task, self.generation, loader, self._done))
        else:
            self._finish(self.generation, *_read(loader))
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
