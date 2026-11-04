"""일반 실행은 DB 자료만 사용하고 명시적인 데모만 메모리 자료로 시작한다."""

from types import SimpleNamespace

import pytest

import setron.ui.app as app_module
from setron.ui.ai_connection import AIProviders
from setron.ui.import_demo import DemoExcel


@pytest.fixture
def startup(monkeypatch):
    calls, errors, windows = [], [], []
    application = SimpleNamespace(
        setApplicationName=lambda value: None,
        aboutToQuit=SimpleNamespace(connect=lambda callback: None),
        primaryScreen=lambda: None, exec=lambda: 0,
    )
    monkeypatch.setattr(app_module, "QApplication", SimpleNamespace(instance=lambda: application))
    monkeypatch.setattr(app_module, "install_theme", lambda application: None)
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda parent, title, text: errors.append(text))

    def create(source, **providers):
        windows.append((source, providers))
        return SimpleNamespace(show=lambda: None, notice=SimpleNamespace(setText=lambda text: None))

    monkeypatch.setattr(app_module, "create_window", create)
    source = object()
    ai = AIProviders(client=SimpleNamespace(close=lambda: None), sheet_extractor=lambda kind, sheet: None)
    values = dict(source=source, ai=ai, saver=object(), sql=object(), management=object(), export=object())

    def load(name, value):
        calls.append(name)
        return value

    monkeypatch.setattr(app_module, "dashboard_source_from_env", lambda: load("source", (source, None)))
    for name, key in (("ai_providers_from_env", "ai"), ("saver_from_env", "saver"),
                      ("sql_exporter_from_env", "sql"), ("gateway_from_env", "management"),
                      ("data_exporter_from_env", "export")):
        monkeypatch.setattr(app_module, name, lambda key=key: load(key, values[key]))
    return SimpleNamespace(calls=calls, errors=errors, windows=windows, values=values)


def test_default_startup_binds_the_same_database_providers(startup):
    assert app_module.main([]) == 0
    assert not startup.errors
    source, providers = startup.windows[0]
    assert source is startup.values["source"]
    for key, name in (("import_saver", "saver"), ("db_exporter", "sql"),
                      ("management_gateway", "management"), ("data_exporter", "export")):
        assert providers[key] is startup.values[name]
    assert providers["sheet_extractor"] is startup.values["ai"].sheet_extractor
    assert providers["excel"] is None and providers["extractor"] is None


@pytest.mark.parametrize("problem", [None, "DB 자료를 읽지 못했습니다: 접속 실패"])
def test_unconfigured_or_failed_database_never_opens_mock_window(startup, monkeypatch, problem):
    monkeypatch.setattr(app_module, "dashboard_source_from_env", lambda: (None, problem))
    assert app_module.main([]) == 1
    assert startup.errors and not startup.windows and not startup.calls
    assert "합성" not in startup.errors[0]


@pytest.mark.parametrize("flag", ["--demo", "--demo-excel"])
def test_explicit_demo_uses_no_database_or_live_ai_providers(startup, flag):
    assert app_module.main([flag]) == 0
    assert not startup.calls and not startup.errors
    source, providers = startup.windows[0]
    assert source is None and isinstance(providers["excel"], DemoExcel)
    assert all(providers[name] is None for name in (
        "import_saver", "db_exporter", "management_gateway", "data_exporter", "sheet_extractor", "ai_client"))
    assert providers["extractor"] is app_module.example_extractor
