"""Windows 실행 설정 파일(setron.env) 읽기를 검증한다 (Jira S15P31B106-77)."""

import sys

import setron.__main__ as entry
from setron.infrastructure.env_file import load_env_file


def test_reads_values_without_overwriting_and_keeps_special_characters(tmp_path):
    path = tmp_path / "setron.env"
    path.write_text("﻿# 주석\n\nSETRON_DB_NAME=setron\nSETRON_DB_PASSWORD=p#ss word=1\n"
                    "SETRON_AI_MODEL=\"gpt 4\"\nSETRON_DB_HOST=새값\n잘못된 줄\n", encoding="utf-8")
    environ = {"SETRON_DB_HOST": "기존값"}

    loaded = load_env_file(path, environ)

    assert loaded == ("SETRON_DB_NAME", "SETRON_DB_PASSWORD", "SETRON_AI_MODEL")
    assert environ == {"SETRON_DB_HOST": "기존값", "SETRON_DB_NAME": "setron",
                       "SETRON_DB_PASSWORD": "p#ss word=1", "SETRON_AI_MODEL": "gpt 4"}


def test_missing_file_changes_nothing(tmp_path):
    environ = {}
    assert load_env_file(tmp_path / "없음.env", environ) == () and environ == {}


def test_only_frozen_startup_reads_the_selected_env_file(tmp_path, monkeypatch):
    calls = []
    bundle = tmp_path / "dist" / "SETRON"
    monkeypatch.setattr(entry, "load_env_file", lambda path: calls.append(path))
    monkeypatch.setattr("setron.ui.app.main", lambda: 0)

    monkeypatch.delattr(sys, "frozen", raising=False)
    assert entry.run() == 0 and calls == []

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "SETRON.exe"))
    assert entry.run() == 0 and calls == [bundle / "setron.env"]
