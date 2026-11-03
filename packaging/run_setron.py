"""PyInstaller가 묶는 시작 스크립트. 실제 시작 처리는 setron.__main__.run()에 있다."""

from setron.__main__ import run

raise SystemExit(run())
