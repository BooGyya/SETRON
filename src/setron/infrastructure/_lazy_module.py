"""자료형을 읽는 동안 외부 SDK를 불러오지 않는 내부 모듈 참조."""

from importlib import import_module


class LazyModule:
    def __init__(self, name):
        self._name = name
        self._module = None

    def __getattr__(self, name):
        if self._module is None:
            self._module = import_module(self._name)
        return getattr(self._module, name)
