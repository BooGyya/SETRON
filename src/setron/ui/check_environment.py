"""업무 화면 개발 전에 PySide6 창 표시와 종료를 확인한다."""

import argparse
import platform
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auto-close-ms",
        type=int,
        default=0,
        help="자동 확인 시 지정한 밀리초 뒤 종료한다. 기본값은 직접 닫기이다.",
    )
    args = parser.parse_args()
    if args.auto_close_ms < 0:
        parser.error("--auto-close-ms는 0 이상이어야 합니다.")

    app = QApplication([sys.argv[0]])
    window = QDialog()
    window.setWindowTitle("SETRON · UI 실행 환경 확인")
    window.resize(520, 240)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(12)

    message = QLabel(
        "이 창이 보이면 PySide6 기본 실행이 정상입니다.\n"
        "아래 버전을 확인한 뒤 닫기 버튼을 눌러 주세요."
    )
    message.setWordWrap(True)
    layout.addWidget(message)
    layout.addWidget(QLabel(f"Python: {platform.python_version()}"))
    layout.addWidget(QLabel(f"PySide6: {pyside_version} / Qt: {qVersion()}"))
    layout.addWidget(QLabel(f"화면 플랫폼: {app.platformName()}"))
    close_button = QPushButton("닫기")
    close_button.clicked.connect(window.accept)
    layout.addWidget(close_button)

    print(
        f"Python {platform.python_version()} | PySide6 {pyside_version} | "
        f"Qt {qVersion()} | platform={app.platformName()}",
        flush=True,
    )
    window.show()
    if args.auto_close_ms:
        QTimer.singleShot(args.auto_close_ms, close_button.click)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
