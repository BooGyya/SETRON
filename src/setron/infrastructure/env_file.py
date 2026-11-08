"""Windows 실행과 DB 초기화에 쓰는 설정 파일(`setron.env`)을 불러온다 (Jira S15P31B106-77).

개발 중에는 `uv run --env-file .env`가 환경변수를 넣어 주지만, 배포한 실행 파일에는 uv가 없다.
실행 파일은 같은 형식의 파일을 읽어 아직 없는 환경변수를 채운다.

형식 (`setron.env.example`과 같다)
- 한 줄에 `이름=값` 하나. 빈 줄과 `#`로 시작하는 줄은 건너뛴다.
- 값은 그대로 쓴다. 비밀번호에 `#`·공백이 있을 수 있으므로 줄 중간의 `#`을 주석으로 보지 않는다.
  값 전체가 같은 따옴표로 감싸져 있을 때만 따옴표를 벗긴다.
- 이미 설정된 환경변수는 덮어쓰지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


def load_env_file(path: str | Path, environ: MutableMapping[str, str] | None = None) -> tuple[str, ...]:
    """``path``의 설정을 ``environ``(기본 os.environ)에 채우고, 새로 넣은 이름을 돌려준다. 파일이 없으면 빈 튜플."""
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return ()
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in environ:
            environ[name] = value
            loaded.append(name)
    return tuple(loaded)
