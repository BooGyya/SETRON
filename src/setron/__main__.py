"""python -m setron 실행 진입점. Windows 실행 파일(packaging/)도 이 run()으로 시작한다."""

import sys
from pathlib import Path

from setron.infrastructure.env_file import load_env_file

ENV_FILE_NAME = "setron.env"


def run() -> int:
    if getattr(sys, "frozen", False):
        # 배포한 실행 파일은 실행 파일 옆의 설정 파일을 읽는다.
        load_env_file(Path(sys.executable).with_name(ENV_FILE_NAME))
    from setron.ui.app import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run())
