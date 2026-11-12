"""숨은 조회 화면은 최신 결과 참조만 보관하고 최초 사용 때 계산한다."""


class ResultAttribute:
    """기존 공개 결과 속성을 읽으면 대기 중 결과를 한 번 반영한다."""

    def __set_name__(self, owner, name):
        self.name = name
        # Shiboken의 속성 조회가 공개 이름의 __dict__ 값을 바로 반환하지 않게 분리한다.
        self.storage = "_result_value_" + name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        instance.ensure_result()
        try:
            return instance.__dict__[self.storage]
        except KeyError:
            raise AttributeError(self.name) from None

    def __set__(self, instance, value):
        instance.__dict__[self.storage] = value


class DeferredResultMixin:
    def queue_result(self, query, snapshot):
        """현재 조회 결과 참조만 교체한다. 화면 계산과 위젯 갱신은 하지 않는다."""
        if (self.state is None or not self.state.is_current(query) or snapshot.query is not query
                or not self.state.catalog.is_view_of(self.source.catalog)):
            return False
        self._pending_result = query, snapshot
        self.snapshot = snapshot
        return True

    def ensure_result(self):
        pending = self.__dict__.get("_pending_result")
        if pending is None or self.__dict__.get("_ensuring_result", False):
            return False
        self._pending_result = None
        self._ensuring_result = True
        try:
            return self.accept_result(*pending)
        finally:
            self._ensuring_result = False

    def showEvent(self, event):
        self.ensure_result()
        super().showEvent(event)
